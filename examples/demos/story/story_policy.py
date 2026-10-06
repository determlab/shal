"""A scripted player for the story's arena steps and its 10-run bench step.

``shal-arena bench --policy`` shape: ``play_with_shal`` and
``play_without_shal`` each take ``(task_path, seed, state_dir)`` and return
``(run_id, answer_record)``. Both drive the card to its nominal input, take
one reading, and answer ``ok`` (or ``open`` when the reading could not be
taken). With SHAL the drive goes through the gate and the reading through a
driver; without it both are raw SCPI.
"""
from __future__ import annotations

from pathlib import Path

from shal_arena.errors import ArenaError
from shal_arena.runner import answer, drive_input, raw_scpi, start_run, take_measurement

DMM_DRIVER = Path(__file__).resolve().parent / "dmm_driver.py"

# each task's own input voltage, as its question text states it
NOMINAL_VOLTS = {"easy": 5.0, "medium": 12.0, "hard": 9.0}


def _volts(task_path: str) -> float:
    return NOMINAL_VOLTS.get(Path(task_path).stem, 5.0)


def play_with_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", _volts(task_path), state_dir=state_dir)
    given = "ok"
    try:
        take_measurement(run_id, "dmm0", DMM_DRIVER, state_dir=state_dir)
    except ArenaError:
        given = "open"
    return run_id, answer(run_id, given, state_dir=state_dir)


def play_without_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", f"VOLT {_volts(task_path)}", state_dir=state_dir)
    given = "ok"
    try:
        raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=state_dir)
    except ArenaError:
        given = "open"
    return run_id, answer(run_id, given, state_dir=state_dir)
