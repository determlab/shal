"""issue #498: README.md and AGENTS.md (the repo root's) each show a `--at`
example for the several-point DMM, and the command they show is real: run
through the CLI on a power-28v run, it measures at that point."""
from __future__ import annotations

import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import PASSING_DMM_DRIVER

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = [_ROOT / "README.md", _ROOT / "AGENTS.md"]
_AT_EXAMPLE = re.compile(r"`(shal-arena measure <run-id> \S+ \S+ --at \S+ --json)`")


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "shal_arena.cli", *args], cwd=cwd,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=60)


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_doc_shows_a_measure_at_example(doc: Path) -> None:
    text = doc.read_text(encoding="utf-8")
    assert _AT_EXAMPLE.search(text), f"{doc.name}: no `shal-arena measure ... --at` example"
    assert "power-28v" in text
    assert "shal-arena demo --json" in text


@pytest.mark.parametrize("doc", _DOCS, ids=lambda p: p.name)
def test_the_documented_at_command_runs(doc: Path, tmp_path: Path) -> None:
    command = _AT_EXAMPLE.search(doc.read_text(encoding="utf-8")).group(1)
    started = _cli(["run", "power-28v", "--state-dir", str(tmp_path), "--json"], tmp_path)
    assert started.returncode == 0, started.stderr
    run_id = json.loads(started.stdout)["run_id"]
    argv = shlex.split(command)[1:]  # drop "shal-arena"
    argv = [run_id if a == "<run-id>" else a for a in argv]
    argv[3] = str(PASSING_DMM_DRIVER)  # the doc's ./driver.py is the player's own
    proc = _cli([*argv, "--state-dir", str(tmp_path)], tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = json.loads(proc.stdout)
    assert out["point"] == argv[argv.index("--at") + 1]
