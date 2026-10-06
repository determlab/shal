"""issue #396: the README's "Write your driver" section is real. The psu and
dmm example drivers pass `check-driver`, and `shal-arena bench` runs on them
through `examples/policy.py`, non-interactively, via the real CLI."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_ARENA_ROOT = Path(__file__).resolve().parents[1]
_EXAMPLES = _ARENA_ROOT / "examples"
_TASK = _ARENA_ROOT / "src" / "shal_arena" / "tasks" / "rail-3v3.yaml"


def _cli(*args: str, cwd: Path) -> dict:
    # work straight from a clone too: put arena/src on the child's path
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(_ARENA_ROOT / "src"), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep)}
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args, "--json"],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"{args}\nstdout: {proc.stdout}\nstderr: {proc.stderr}"
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True, proc.stdout
    return doc


def test_readme_has_write_your_driver_section() -> None:
    text = (_ARENA_ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Write your driver" in text


@pytest.mark.parametrize("address, example", [("psu0", "psu"), ("dmm0", "dmm")])
def test_example_driver_passes_check_and_measures(
        tmp_path: Path, address: str, example: str) -> None:
    run_id = _cli("run", str(_TASK), "--seed", "1", cwd=tmp_path)["run_id"]
    driver = str(_EXAMPLES / example / "driver.py")
    checked = _cli("check-driver", run_id, address, driver, cwd=tmp_path)
    assert checked["passed"] is True, checked["problems"]
    if example == "dmm":
        _cli("measure", run_id, address, driver, cwd=tmp_path)


def test_bench_runs_on_the_examples(tmp_path: Path) -> None:
    doc = _cli("bench", str(_TASK), "--runs", "10", "--policy",
               str(_EXAMPLES / "policy.py"), cwd=tmp_path)
    assert doc["with_shal"]["runs"] == 10
    assert doc["without_shal"]["runs"] == 10
