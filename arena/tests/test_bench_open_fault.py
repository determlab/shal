"""issue #395: ``shal-arena bench`` records a `MeasurementFailed` (the `open`
fault, unreachable by construction) as a failed measurement and keeps going,
instead of the whole benchmark crashing.

A naive policy (the normal thing to write — see ``README.md``'s own worked
example) calls `take_measurement`/`raw_scpi` without wrapping it; on a task
whose only fault is `open`, EVERY seed realizes it, so this used to crash on
the very first run of either side.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shal_arena.bench import MIN_RUNS, run_benchmark, run_side
from shal_arena.runner import answer, drive_input, raw_scpi, start_run, take_measurement

from .conftest import PASSING_DMM_DRIVER

# A card whose ONLY fault is `open` -- random.choice([open]) is `open` for
# every seed, so this reproduces the bug on every single run, not by luck.
_OPEN_ONLY_CARD = """\
arena_card: 1
id: open-only
description: "test card: the only fault is open"
inputs:
  vin: {nominal_v: 5.0}
rails:
  r1: {from: vin, nominal_v: 3.3, tol_pct: 3, test_point: tp1, min_input_v: 4.5}
damage:
  - {input: vin, above_v: 6.0, effect: destroyed, source: "test datasheet"}
faults:
  - {id: open, rail: r1}
"""

_OPEN_ONLY_TASK = """\
arena_task: 1
id: open-only-task
title: "always open"
level: easy
card: card.yaml
instruments:
  - case: scpi-psu
    address: psu0
    drives: card.vin
  - case: dmm
    address: dmm0
    probe: card.tp1
question:
  text: "name the fault"
  answer: {kind: enum, values: [open]}
seed: 1
"""


@pytest.fixture
def open_fault_task(tmp_path: Path) -> Path:
    (tmp_path / "card.yaml").write_text(_OPEN_ONLY_CARD, encoding="utf-8")
    task_path = tmp_path / "task.yaml"
    task_path.write_text(_OPEN_ONLY_TASK, encoding="utf-8")
    return task_path


def _play_naive_with_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    """A policy that does NOT wrap `take_measurement` -- the normal, naive
    thing to write; bench itself must survive the `open` fault, not the
    policy author."""
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    return run_id, answer(run_id, "ok", state_dir=state_dir)


def _play_naive_without_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 5.0", state_dir=state_dir)
    raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=state_dir)
    return run_id, answer(run_id, "ok", state_dir=state_dir)


def test_run_side_survives_measurement_failed_and_counts_it(
    open_fault_task: Path, tmp_path: Path
) -> None:
    results = run_side(str(open_fault_task), _play_naive_with_shal, runs=MIN_RUNS,
                       seed_base=0, state_dir=tmp_path / "state")
    assert len(results) == MIN_RUNS   # finished -- did not crash partway through
    assert all(r["measurement_failed"] for r in results)
    assert all(r["correct"] is False for r in results)
    assert all(r["run_id"] is None for r in results)   # play raised before returning one


def test_bench_cli_json_counts_measurement_failures(tmp_path: Path) -> None:
    """Agent path: `shal-arena bench --runs 10 --json` with the `open` fault
    active, reading the failure count straight from --json."""
    import json
    import subprocess
    import sys

    (tmp_path / "card.yaml").write_text(_OPEN_ONLY_CARD, encoding="utf-8")
    task_path = tmp_path / "task.yaml"
    task_path.write_text(_OPEN_ONLY_TASK, encoding="utf-8")
    policy_path = tmp_path / "policy.py"
    policy_path.write_text(
        "from shal_arena.runner import answer, drive_input, raw_scpi, start_run, take_measurement\n"
        f"PASSING_DMM_DRIVER = {str(PASSING_DMM_DRIVER)!r}\n"
        "def play_with_shal(task_path, seed, state_dir):\n"
        "    run_id = start_run(task_path, seed=seed, state_dir=state_dir)['run_id']\n"
        "    drive_input(run_id, 'psu0', 5.0, state_dir=state_dir)\n"
        "    take_measurement(run_id, 'dmm0', PASSING_DMM_DRIVER, state_dir=state_dir)\n"
        "    return run_id, answer(run_id, 'ok', state_dir=state_dir)\n"
        "def play_without_shal(task_path, seed, state_dir):\n"
        "    run_id = start_run(task_path, seed=seed, state_dir=state_dir)['run_id']\n"
        "    raw_scpi(run_id, 'psu0', 'VOLT 5.0', state_dir=state_dir)\n"
        "    raw_scpi(run_id, 'dmm0', 'MEAS:VOLT:DC?', state_dir=state_dir)\n"
        "    return run_id, answer(run_id, 'ok', state_dir=state_dir)\n",
        encoding="utf-8",
    )

    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "bench", str(task_path), "--runs",
         str(MIN_RUNS), "--policy", str(policy_path), "--state-dir", str(tmp_path / "cli_state"),
         "--json"],
        capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    for side in ("with_shal", "without_shal"):
        assert doc[side]["runs"] == MIN_RUNS
        assert doc[side]["measurement_failures"] == MIN_RUNS


def test_run_benchmark_does_not_crash_on_the_open_fault(open_fault_task: Path,
                                                         tmp_path: Path) -> None:
    result = run_benchmark(str(open_fault_task), play_with_shal=_play_naive_with_shal,
                           play_without_shal=_play_naive_without_shal, runs=MIN_RUNS,
                           state_dir=tmp_path / "state")
    assert result["ok"] is True
    assert result["with_shal"]["measurement_failures"] == MIN_RUNS
    assert result["without_shal"]["measurement_failures"] == MIN_RUNS
