"""issue #397: `bench` has a built-in default policy, so `shal-arena bench
--runs 10` works with one command and no `--policy` file -- the Done-when
runs this exact CLI form, non-interactively, through a subprocess (same
style `test_readme_commands.py` already uses)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_TIMEOUT = 60


def test_bench_runs_10_with_no_policy_and_no_task(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "bench", "--runs", "10", "--json"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=_TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    assert doc["with_shal"]["runs"] == 10
    assert doc["without_shal"]["runs"] == 10


def test_bench_default_policy_is_importable_from_python(tmp_path: Path) -> None:
    from shal_arena.bench import DEFAULT_POLICY, DEFAULT_TASK, MIN_RUNS, run_benchmark

    result = run_benchmark(str(DEFAULT_TASK), play_with_shal=DEFAULT_POLICY.play_with_shal,
                           play_without_shal=DEFAULT_POLICY.play_without_shal,
                           runs=MIN_RUNS, state_dir=tmp_path)
    assert result["ok"] is True
