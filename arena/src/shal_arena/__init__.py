"""shal-arena — SHAL Arena's runner and task format (issue #310).

    import shal_arena
    result = shal_arena.start_run("tasks/rail-3v3.yaml")
    shal_arena.check_instrument_driver(result["run_id"], "psu0", "driver.py")
    shal_arena.answer(result["run_id"], "low_voltage")

Or from the shell: ``shal-arena run tasks/rail-3v3.yaml --json``.
"""
from .errors import ArenaError, CheckCouldNotRun, MeasurementFailed, TaskFormatError, UnknownRun
from .loader import LoadedTask, load_task
from .runner import NotSupported, answer, check_instrument_driver, start_run, take_measurement

__all__ = [
    "ArenaError",
    "TaskFormatError",
    "UnknownRun",
    "CheckCouldNotRun",
    "MeasurementFailed",
    "NotSupported",
    "LoadedTask",
    "load_task",
    "start_run",
    "check_instrument_driver",
    "take_measurement",
    "answer",
]
