"""An MCP stdio server over the ``shal-arena`` CLI, for the two-agent check (issue #317).

    python arena/ci/arena_mcp.py --state-dir DIR

The arena has no MCP server of its own yet; the package ships the CLI. This
file is the thinnest MCP surface that is still the real game: each tool is
one ``shal-arena <command> ... --json`` call, run as a child process, and
returns that command's own JSON document as text. A command that exits
non-zero is an MCP tool error whose text is the CLI's own
``{"ok": false, "error": {"type", "message", "fix"}}``. No second
implementation of any command lives here, so the CLI and MCP paths cannot
disagree about a score.

It lives in ``arena/ci/``, not in the package: it is test scaffolding for
issue #317. A shipped ``shal-arena mcp`` command is a separate decision.

Each tool states its side effect in its description and in its MCP
annotations (agent-native rule 4). The tools are the ones a player needs to
finish a task: ``tasks``, ``run``, ``check_driver``, ``measure``, ``drive``,
``call``, ``answer``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_STR = {"type": "string"}
_RUN_ID = {"type": "string", "description": "the run_id `run` returned"}
_ADDR = {"type": "string", "description": "the instrument address from `run`'s output"}
_DRIVER = {"type": "string", "description": "path to your driver.py"}

#: name -> (CLI subcommand, positional params in order, optional params as
#: {name: flag}, input schema properties, side effect, read-only?)
TOOLS: dict[str, dict[str, Any]] = {
    "tasks": {
        "cli": "tasks", "positional": [], "options": {}, "stateless": True,
        "properties": {}, "side_effect": "none",
        "description": "List the packaged tasks; each `name` is what `run` takes.",
    },
    "run": {
        "cli": "run", "positional": ["task"], "options": {"seed": "--seed"},
        "properties": {"task": {"type": "string",
                                "description": "a packaged task name, e.g. rail-3v3"},
                       "seed": {"type": "integer",
                                "description": "override the task's seed"}},
        "side_effect": "write",
        "description": "Start a run; returns run_id, the task, the question and each "
                       "instrument's datasheet. Writes run state.",
    },
    "check_driver": {
        "cli": "check-driver", "positional": ["run_id", "instrument", "driver_path"],
        "options": {},
        "properties": {"run_id": _RUN_ID, "instrument": _ADDR, "driver_path": _DRIVER},
        "side_effect": "write",
        "description": "Check your driver.py against the instrument's case. Counts one "
                       "turn in the run state.",
    },
    "measure": {
        "cli": "measure", "positional": ["run_id", "instrument", "driver_path"],
        "options": {"at": "--at"},
        "properties": {"run_id": _RUN_ID, "instrument": _ADDR, "driver_path": _DRIVER,
                       "at": {"type": "string",
                              "description": "the test point, when the instrument "
                                             "probes several"}},
        "side_effect": "write",
        "description": "Take your own reading through your driver.py. Writes the sim log "
                       "and counts one turn.",
    },
    "drive": {
        "cli": "drive", "positional": ["run_id", "instrument", "volts"], "options": {},
        "properties": {"run_id": _RUN_ID, "instrument": _ADDR,
                       "volts": {"type": "number"}},
        "side_effect": "actuator",
        "description": "Apply a voltage to the card input this instrument drives. Acts on "
                       "the simulated card; the gate refuses a voltage that would damage it.",
    },
    "call": {
        "cli": "call", "positional": ["run_id", "instrument", "driver_path", "op"],
        "options": {"at": "--at"}, "rest": "args",
        "properties": {"run_id": _RUN_ID, "instrument": _ADDR, "driver_path": _DRIVER,
                       "op": {"type": "string", "description": "the op name, e.g. set_relay"},
                       "args": {"type": "array", "items": _STR,
                                "description": "positional op arguments, e.g. [\"0\", \"true\"]"},
                       "at": {"type": "string"}},
        "side_effect": "write",
        "description": "Run any op of your driver through SHAL's gate (e.g. a relay's "
                       "set_relay). The op's own side effect applies.",
    },
    "answer": {
        "cli": "answer", "positional": ["run_id", "value"], "options": {},
        "properties": {"run_id": _RUN_ID,
                       "value": {"type": "string",
                                 "description": "one of the task's answer values"}},
        "side_effect": "write",
        "description": "Answer the question and close the run. Writes the record and the "
                       "score file; cannot be undone.",
    },
}


def tool_schema(name: str) -> dict[str, Any]:
    spec = TOOLS[name]
    required = list(spec["positional"])
    return {"type": "object", "properties": spec["properties"], "required": required,
            "additionalProperties": False}


def cli_argv(name: str, arguments: dict[str, Any] | None, *, state_dir: str | Path,
             prefix: list[str] | None = None) -> list[str]:
    """The exact ``shal-arena`` command line one tool call is. ``prefix`` is
    how to start ``shal-arena`` (default: this interpreter's ``-m
    shal_arena.cli``, the same entry point the console script calls)."""
    if name not in TOOLS:
        raise KeyError(f"unknown tool {name!r}; tools: {', '.join(TOOLS)}")
    spec, arguments = TOOLS[name], dict(arguments or {})
    missing = [p for p in spec["positional"] if p not in arguments]
    if missing:
        raise ValueError(f"{name}: missing argument(s) {', '.join(missing)}")
    argv = list(prefix or [sys.executable, "-m", "shal_arena.cli"]) + [spec["cli"]]
    argv += [str(arguments[p]) for p in spec["positional"]]
    if "rest" in spec:
        argv += [str(a) for a in arguments.get(spec["rest"]) or []]
    for key, flag in spec["options"].items():
        if arguments.get(key) is not None:
            argv += [flag, str(arguments[key])]
    argv.append("--json")
    # `tasks` reads only package data and takes no --state-dir
    return argv if spec.get("stateless") else argv + ["--state-dir", str(state_dir)]


def run_tool(name: str, arguments: dict[str, Any] | None, *, state_dir: str | Path,
             prefix: list[str] | None = None) -> tuple[int, str]:
    """Run one tool as its CLI command: ``(exit code, stdout)``."""
    proc = subprocess.run(cli_argv(name, arguments, state_dir=state_dir, prefix=prefix),
                          capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout.strip() or json.dumps(
        {"ok": False, "error": {"type": "NoOutput", "message": proc.stderr.strip(),
                                "fix": "run the same shal-arena command by hand to see why"}})


def _build_server(state_dir: Path):
    import anyio
    import mcp.types as types
    from mcp.server import Server

    server: Server = Server("shal-arena")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [types.Tool(
                    name=name, description=f"{spec['description']} "
                                           f"(side_effect: {spec['side_effect']})",
                    inputSchema=tool_schema(name),
                    annotations=types.ToolAnnotations(
                        readOnlyHint=spec["side_effect"] == "none",
                        destructiveHint=spec["side_effect"] == "actuator"))
                for name, spec in TOOLS.items()]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None):
        code, text = await anyio.to_thread.run_sync(
            lambda: run_tool(name, arguments, state_dir=state_dir))
        if code != 0:
            # the low-level server turns a raised error into isError=True
            raise RuntimeError(text)
        return [types.TextContent(type="text", text=text)]

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arena_mcp.py", description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, default=Path(".shal-arena"),
                        help="where run state lives, as `shal-arena --state-dir`")
    args = parser.parse_args(argv)
    from mcp.server.stdio import stdio_server

    server = _build_server(args.state_dir.resolve())

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
