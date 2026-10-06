"""issue #431 (p1 bug): `fault.harness_for_run` used to seed the sim
model's own ripple RNG from the run's own `seed` alone -- the same value
on every single call, in every process, so the `noise` fault read back
the exact same "random" value every time a player measured it. Fixed by
mixing in a per-call `nonce` (`state.turns`, already incremented before
each measurement). This file proves the fix with the real sim across
real, separate CLI processes -- not a hand-written sim log."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.data import run_payload

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

_CARD = load_task(str(SAMPLE_TASK)).card


def _noise_seed(limit: int = 300) -> int:
    for seed in range(limit):
        if fault_mod.realized_fault(_CARD, seed).fault_id == "noise":
            return seed
    raise AssertionError(f"no seed under {limit} realizes the noise fault")


def test_three_in_process_measurements_of_noise_are_not_all_equal(tmp_path: Path) -> None:
    seed = _noise_seed()
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)

    readings = [take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER,
                                 state_dir=tmp_path)["reading"] for _ in range(3)]
    assert len({round(r, 4) for r in readings}) > 1, readings


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def test_three_separate_cli_processes_measuring_noise_are_not_all_equal(
        tmp_path: Path) -> None:
    """The Done-when's own repro: `shal-arena run`, then `measure` 3 times
    as 3 separate CLI invocations (3 separate Python processes) -- the
    exact shape issue #431 reports (seed 5, "3.335183, 3 times")."""
    seed = _noise_seed()
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--seed", str(seed),
                        "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]
    _run_cli("drive", run_id, "psu0", "5.0", "--state-dir", str(state_dir), "--json")

    readings = []
    for _ in range(3):
        proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                        "--state-dir", str(state_dir), "--json")
        assert proc.returncode == 0, proc.stderr
        readings.append(json.loads(proc.stdout)["reading"])
    assert len({round(r, 4) for r in readings}) > 1, readings


def test_noise_sentence_range_comes_from_real_separate_process_readings(
        tmp_path: Path) -> None:
    """issue #431's second Done-when line: the noise sentence's range code
    is exercised against the real sim across real CLI calls, not a
    hand-written sim log (test_ui_followup.py's own
    `test_answer_sentence_names_the_range_across_repeated_noisy_reads`)."""
    seed = _noise_seed()
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--seed", str(seed),
                        "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]
    _run_cli("drive", run_id, "psu0", "5.0", "--state-dir", str(state_dir), "--json")
    for _ in range(3):
        _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                 "--state-dir", str(state_dir), "--json")
    answer(run_id, "noise", state_dir=state_dir)

    payload = run_payload(run_id, state_dir=state_dir)
    readings = [e["detail"]["value"] for e in payload["timeline"] if e["kind"] == "reading"]
    assert len({round(r, 2) for r in readings}) > 1, readings
    sentence = payload["answer_sentence"]
    assert "across reads" in sentence


def test_two_different_runs_with_the_same_turn_count_still_differ(tmp_path: Path) -> None:
    """The nonce (`state.turns`) alone must not make every run look the
    same -- it is mixed with the run's own `seed`, so two different noise
    runs read differently even on their own first measurement each."""
    seeds = [s for s in range(300)
            if fault_mod.realized_fault(_CARD, s).fault_id == "noise"][:2]
    assert len(seeds) >= 2, "need two distinct noise seeds to prove this"
    first_readings = []
    for seed in seeds:
        run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
        drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
        first_readings.append(
            take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)["reading"])
    assert first_readings[0] != first_readings[1]
