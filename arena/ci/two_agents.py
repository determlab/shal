"""Two agents finish an arena task through the CLI with --json and through MCP (issue #317).

    python arena/ci/two_agents.py [--json]

Exit 0 only when all four plays (two agents x two surfaces) close the run,
are not disqualified, write a score file, and agree on the score.

**What runs here, honestly.** CI has no Claude Code or Codex session: both
need an account and an API key, and their answers are not repeatable. So
each agent is a scripted stand-in that does what that agent does on each
surface, and nothing more:

- CLI: the agent runs ``shal-arena <command> ... --json`` as a child process
  (Claude Code's Bash tool, Codex's shell tool) and reads stdout as JSON.
- MCP: the agent's host reads the MCP server from its own config file --
  Claude Code from ``.mcp.json`` (``mcpServers``), Codex from
  ``~/.codex/config.toml`` (``[mcp_servers.<name>]``) -- starts it over stdio,
  and calls its tools. The server is `arena_mcp.py`. The config file is
  written in each vendor's format and read back with a plain JSON/TOML
  parser, so a broken snippet fails here.

The decisions (which task, what to drive, which driver to copy, what to
answer) are one policy, `solve`, the same for both agents: it reads the run's
own JSON, copies the README's minimal example driver for each instrument it
uses, drives the input the question names, measures the probe, and answers
from the rail limits the run returns. What this proves is that an agent with
only these two surfaces can finish a task. It does not prove how well a real
model plays; that needs a live run with a real agent, outside CI.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import arena_mcp  # noqa: E402

AGENTS = ("claude-code", "codex")
SURFACES = ("cli", "mcp")
TASK, SEED = "rail-3v3", 1
SERVER_NAME = "shal-arena"
#: the README's "Write your driver" templates, per case
EXAMPLES = _HERE.parent / "examples"
EXAMPLE_DRIVERS = {"scpi-psu": EXAMPLES / "minimal_psu_driver.py",
                   "dmm": EXAMPLES / "minimal_dmm_driver.py"}
#: score fields that differ on every run by design (seed_scores.DROPPED_FIELDS)
_CLOCK_FIELDS = ("duration_s", "record_sha256")

Call = Callable[[str, dict[str, Any]], dict[str, Any]]


class StepFailed(Exception):
    """One step of a play did not do what the docs say it does."""


# --- the policy: one for both agents ------------------------------------------

def _volts_from_question(question: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*V\b", question)
    if not m:
        raise StepFailed(f"no voltage in the question: {question!r}")
    return float(m.group(1))


def _diagnose(reading: float, rail: dict[str, Any], values: list[str]) -> str:
    if rail["min_v"] <= reading <= rail["max_v"]:
        return "ok"
    if abs(reading) < 0.05 * rail["nominal_v"] and "open" in values:
        return "open"
    if reading < rail["min_v"] and "low_voltage" in values:
        return "low_voltage"
    return "noise" if "noise" in values else values[0]


def solve(call: Call, workdir: Path, *, task: str = TASK, seed: int = SEED) -> dict[str, Any]:
    """Play one task end to end through ``call(tool, arguments) -> JSON``."""
    names = [t["name"] for t in call("tasks", {})["tasks"]]
    if task not in names:
        raise StepFailed(f"`tasks` does not list {task!r}: {names}")
    run = call("run", {"task": task, "seed": seed})
    run_id = run["run_id"]
    drivers: dict[str, str] = {}
    for inst in run["instruments"]:
        if inst["case"] in EXAMPLE_DRIVERS:
            dest = workdir / f"{inst['address']}_driver.py"
            shutil.copyfile(EXAMPLE_DRIVERS[inst["case"]], dest)
            drivers[inst["address"]] = str(dest)

    for inst in run["instruments"]:
        if "drives" in inst:
            call("drive", {"run_id": run_id, "instrument": inst["address"],
                           "volts": _volts_from_question(run["task"]["question"])})
    probe = next(i for i in run["instruments"] if "probe" in i)
    checked = call("check_driver", {"run_id": run_id, "instrument": probe["address"],
                                    "driver_path": drivers[probe["address"]]})
    if not checked.get("passed"):
        raise StepFailed(f"check-driver did not pass: {checked.get('problems')}")
    reading = call("measure", {"run_id": run_id, "instrument": probe["address"],
                               "driver_path": drivers[probe["address"]]})["reading"]
    rail = next(r for r in run["rails"] if probe["probe"].endswith(r["test_point"]))
    given = _diagnose(reading, rail, run["task"]["answer"]["values"])
    closed = call("answer", {"run_id": run_id, "value": given})
    return {"ok": closed["ok"], "run_id": run_id, "given": given, "reading": reading,
            "disqualified": closed["disqualified"], "score": closed["score"]}


# --- the two surfaces ---------------------------------------------------------

def cli_caller(state_dir: Path, prefix: list[str] | None = None) -> Call:
    """``shal-arena <command> --json`` as a child process, stdout parsed."""
    def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        code, out = arena_mcp.run_tool(tool, arguments, state_dir=state_dir, prefix=prefix)
        try:
            doc = json.loads(out)
        except json.JSONDecodeError as e:
            raise StepFailed(f"shal-arena {tool}: stdout is not one JSON document: "
                             f"{out[:200]!r}") from e
        if code != 0 or not doc.get("ok"):
            raise StepFailed(f"shal-arena {tool} exited {code}: {out[:400]}")
        return doc
    return call


def write_config(agent: str, workdir: Path) -> Path:
    """Register `arena_mcp.py` the way this agent's host reads MCP servers."""
    command = sys.executable
    args = [str(_HERE / "arena_mcp.py"), "--state-dir", str(workdir / ".shal-arena")]
    workdir.mkdir(parents=True, exist_ok=True)
    if agent == "claude-code":
        path = workdir / ".mcp.json"
        path.write_text(json.dumps({"mcpServers": {SERVER_NAME: {
            "command": command, "args": args}}}, indent=2), encoding="utf-8")
    elif agent == "codex":
        path = workdir / "config.toml"
        # a TOML basic string takes JSON's escapes, so json.dumps quotes it right
        path.write_text(f"[mcp_servers.{SERVER_NAME}]\n"
                        f"command = {json.dumps(command)}\n"
                        f"args = [{', '.join(json.dumps(a) for a in args)}]\n",
                        encoding="utf-8")
    else:
        raise StepFailed(f"unknown agent {agent!r}; agents: {', '.join(AGENTS)}")
    return path


def server_from_config(agent: str, path: Path) -> list[str]:
    """The command line this agent's host starts the MCP server with."""
    text = path.read_text(encoding="utf-8")
    if agent == "claude-code":
        entry = json.loads(text)["mcpServers"][SERVER_NAME]
    elif agent == "codex":
        try:
            import tomllib
        except ModuleNotFoundError as e:  # Python 3.10
            raise StepFailed("reading Codex's config.toml needs Python 3.11+ (tomllib)") from e
        entry = tomllib.loads(text)["mcp_servers"][SERVER_NAME]
    else:
        raise StepFailed(f"unknown agent {agent!r}; agents: {', '.join(AGENTS)}")
    return [entry["command"], *entry.get("args", [])]


def _play_mcp(command: list[str], workdir: Path) -> dict[str, Any]:
    from anyio.from_thread import start_blocking_portal
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=command[0], args=command[1:], cwd=str(workdir))
    with start_blocking_portal() as portal, \
            portal.wrap_async_context_manager(stdio_client(params)) as (read, write), \
            portal.wrap_async_context_manager(ClientSession(read, write)) as session:
        portal.call(session.initialize)
        listed = {t.name for t in portal.call(session.list_tools).tools}
        missing = set(arena_mcp.TOOLS) - listed
        if missing:
            raise StepFailed(f"MCP server does not list {sorted(missing)}")

        def call(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
            result = portal.call(session.call_tool, tool, arguments)
            text = "".join(getattr(c, "text", "") for c in result.content)
            if result.isError:
                raise StepFailed(f"MCP {tool} failed: {text[:400]}")
            return json.loads(text)

        return solve(call, workdir)


def play(agent: str, surface: str, workdir: str | Path) -> dict[str, Any]:
    """One agent finishes `TASK` on one surface. Adds ``agent``, ``surface``
    and ``score_path`` (the score file `answer` wrote) to `solve`'s result."""
    workdir = Path(workdir).resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    state_dir = workdir / ".shal-arena"
    if agent not in AGENTS:
        raise StepFailed(f"unknown agent {agent!r}; agents: {', '.join(AGENTS)}")
    if surface == "cli":
        result = solve(cli_caller(state_dir), workdir)
    elif surface == "mcp":
        command = server_from_config(agent, write_config(agent, workdir))
        result = _play_mcp(command, workdir)
    else:
        raise StepFailed(f"unknown surface {surface!r}; surfaces: {', '.join(SURFACES)}")
    score_path = state_dir / f"{result['run_id']}.score.json"
    return {"agent": agent, "surface": surface, **result, "score_path": str(score_path)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="two_agents.py", description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print one JSON document")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="where each play's files go (default: a temp folder)")
    args = parser.parse_args(argv)
    root = args.workdir or Path(tempfile.mkdtemp(prefix="two-agents-"))
    plays, problems = [], []
    for agent in AGENTS:
        for surface in SURFACES:
            try:
                r = play(agent, surface, root / f"{agent}-{surface}")
            except Exception as e:  # report every cell, then fail
                problems.append(f"{agent} via {surface}: {type(e).__name__}: {e}")
                continue
            plays.append(r)
            if not r["ok"] or r["disqualified"] or not Path(r["score_path"]).is_file():
                problems.append(f"{agent} via {surface}: run not completed: {r}")
    scores = {json.dumps({k: v for k, v in p["score"].items() if k not in _CLOCK_FIELDS},
                         sort_keys=True) for p in plays}
    if len(scores) > 1:
        problems.append(f"the four plays disagree on the score: {sorted(scores)}")
    summary = {"ok": not problems, "task": TASK, "seed": SEED, "problems": problems,
               "plays": [{k: p[k] for k in ("agent", "surface", "run_id", "given",
                                            "disqualified", "score_path")} for p in plays]}
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        for p in plays:
            print(f"two-agents: {p['agent']} via {p['surface']}: answered {p['given']!r}, "
                  f"disqualified={p['disqualified']}, score {p['score_path']}")
        for line in problems:
            print(f"two-agents: FAIL {line}")
    return 0 if not problems else 1


if __name__ == "__main__":
    sys.exit(main())
