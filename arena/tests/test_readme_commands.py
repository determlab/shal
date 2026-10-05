"""issue #346: a cold agent that reads only `arena/README.md` can build a
rack and play a task. Parses every ```bash fenced block in the README and
runs each `shal-arena` command it names through the real CLI (subprocess,
no prompt, exactly like `test_cli.py` does), in a fresh temp dir, asserting
exit 0 and valid `--json` output — proving the commands the README prints
are real, copy-pasteable, and still work, not stale prose."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

_ARENA_ROOT = Path(__file__).resolve().parents[1]
_README = _ARENA_ROOT / "README.md"
_REFERENCE_DRIVER = _ARENA_ROOT / "examples" / "reference_driver" / "driver.py"

_BASH_BLOCK_RE = re.compile(r"```bash\n(.*?)```", re.DOTALL)
_TIMEOUT = 60


def _readme_bash_commands() -> list[str]:
    """Every non-comment, non-blank line inside a ```bash fenced block of
    the README, in document order."""
    text = _README.read_text(encoding="utf-8")
    commands = []
    for block in _BASH_BLOCK_RE.findall(text):
        for line in block.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                commands.append(line)
    return commands


def _resolve_repo_paths(tokens: list[str]) -> list[str]:
    """A bare relative path the README wrote assuming cwd is `arena/`
    (a task yaml, the reference driver) is resolved against `_ARENA_ROOT`
    so the command still finds it when run from a temp dir; anything else
    (flags, bare output filenames meant to land in the temp dir) passes
    through unchanged."""
    resolved = []
    for tok in tokens:
        candidate = _ARENA_ROOT / tok
        if "/" in tok and not tok.startswith("-") and candidate.is_file():
            resolved.append(str(candidate))
        else:
            resolved.append(tok)
    return resolved


def _run_cli(tokens: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *tokens],
        cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TIMEOUT)


def test_reference_driver_exists_and_is_copyable() -> None:
    assert _REFERENCE_DRIVER.is_file(), (
        "arena/examples/reference_driver/driver.py must exist (issue #346 Done-when)")


def test_every_readme_bash_command_runs_and_prints_valid_json(tmp_path: Path) -> None:
    commands = [c for c in _readme_bash_commands() if c.startswith("shal-arena ")]
    assert commands, "README should document at least one shal-arena command"

    run_id: str | None = None
    for command in commands:
        tokens = shlex.split(command)[1:]  # drop the leading "shal-arena"
        tokens = [run_id if (t == "<run-id>" and run_id) else t for t in tokens]
        assert "<run-id>" not in tokens, (
            f"no run id captured yet for the README command: {command!r}")
        tokens = _resolve_repo_paths(tokens)

        proc = _run_cli(tokens, cwd=tmp_path)
        assert proc.returncode == 0, f"{command}\nstderr: {proc.stderr}"
        doc = json.loads(proc.stdout)
        assert doc.get("ok") is True, f"{command}\n{proc.stdout}"

        if tokens and tokens[0] == "run":
            run_id = doc["run_id"]


def test_reference_driver_passes_check_driver(tmp_path: Path) -> None:
    """issue #346 Done-when: `shal-arena check-driver` accepts the reference
    driver; the JSON it prints reads `"ok": true` (as every successful
    `shal-arena` command's does — `check-driver`'s own pass/fail bit is the
    separate `"passed"` key, asserted too since this is the one case the
    reference driver exists to make true)."""
    run_proc = _run_cli(
        ["run", str(_ARENA_ROOT / "src" / "shal_arena" / "tasks" / "rail-3v3.yaml"), "--json"],
        cwd=tmp_path)
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    check_proc = _run_cli(
        ["check-driver", run_id, "psu0", str(_REFERENCE_DRIVER), "--json"], cwd=tmp_path)
    assert check_proc.returncode == 0, check_proc.stderr
    doc = json.loads(check_proc.stdout)
    assert doc["ok"] is True
    assert doc["passed"] is True, doc["problems"]
