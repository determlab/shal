"""Minimal ``--policy`` file for ``shal-arena bench``: plays rail-3v3 with the
example drivers next to it (``psu/driver.py``, ``dmm/driver.py``) and without
SHAL (raw SCPI). Replace the fixed answers with your own agent's reasoning."""
from __future__ import annotations

from pathlib import Path

from shal_arena.errors import MeasurementFailed
from shal_arena.runner import (
    answer,
    check_instrument_driver,
    drive_input,
    raw_scpi,
    start_run,
    take_measurement,
)

HERE = Path(__file__).resolve().parent
PSU_DRIVER = HERE / "psu" / "driver.py"
DMM_DRIVER = HERE / "dmm" / "driver.py"


def play_with_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    check_instrument_driver(run_id, "psu0", PSU_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", DMM_DRIVER, state_dir=state_dir)
    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    given = "ok"
    try:
        take_measurement(run_id, "dmm0", DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:  # the `open` fault: the instrument is unreachable
        given = "open"
    return run_id, answer(run_id, given, state_dir=state_dir)


def play_without_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 5.0", state_dir=state_dir)
    given = "ok"
    try:
        raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=state_dir)
    except MeasurementFailed:
        given = "open"
    return run_id, answer(run_id, given, state_dir=state_dir)
