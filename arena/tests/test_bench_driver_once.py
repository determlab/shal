"""issue #394: ``shal-arena bench`` loads a user ``driver.py`` once.

Before this fix, ``runner._import_driver_file`` re-executed the file on
every ``take_measurement``/``check_instrument_driver`` call — a fresh class
object each time — so the registry saw a second, distinct candidate for the
same ``compatible`` on the SECOND bench run and raised "claimed by 2
drivers". Without this fix, the driver below (deliberately plain, no
``override=True``) makes ``run_benchmark`` fail partway through a batch of
``MIN_RUNS`` runs; with it, every run finishes clean.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from shal import registry

from shal_arena.bench import MIN_RUNS, run_benchmark
from shal_arena.errors import MeasurementFailed
from shal_arena.runner import answer, drive_input, raw_scpi, start_run, take_measurement

from .conftest import SAMPLE_TASK

_COMPATIBLE = "arena,bench-dmm1"  # the packaged "dmm" case's own fixed compatible


@pytest.fixture(autouse=True)
def _clean_registry_slot():
    """This test's own driver does NOT use override=True (that is the whole
    point), so it must not inherit whatever candidate an EARLIER test in this
    same process already left under `_COMPATIBLE` (every other driver
    fixture in this suite uses override=True specifically to paper over
    that cross-test pollution -- see passing_dmm_driver.py's own docstring).
    Starts this test from a clean slot and restores whatever was there after,
    so this test's own registry state never leaks into a later test either."""
    before = list(registry._entries.get(_COMPATIBLE, []))
    registry._entries[_COMPATIBLE] = []
    try:
        yield
    finally:
        registry._entries[_COMPATIBLE] = before

_DMM_DRIVER_NO_OVERRIDE = '''\
"""A plain driver.py, exactly as a player would write one. issue #394: this
must be loadable across every run of one bench batch without the registry
ever seeing it as more than one driver."""
from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class PlainBenchDmm(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(PlainBenchDmm)
'''


def test_driver_py_has_no_override_true() -> None:
    # Sanity on the fixture itself: this test proves the FIX, not a driver
    # that was already dodging the bug the issue describes.
    assert "override=True" not in _DMM_DRIVER_NO_OVERRIDE


def test_bench_runs_a_plain_driver_with_no_override_true(tmp_path: Path) -> None:
    driver_path = tmp_path / "driver.py"
    driver_path.write_text(_DMM_DRIVER_NO_OVERRIDE, encoding="utf-8")

    def _play_with_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
        run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
        drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
        given = "ok"
        # Only the 'open' fault's live read failure is expected here -- NOT
        # ArenaError broadly, which would also catch (and silently paper
        # over) the exact CheckCouldNotRun("... claimed by 2 drivers ...")
        # this test exists to catch.
        try:
            take_measurement(run_id, "dmm0", driver_path, state_dir=state_dir)
        except MeasurementFailed:
            given = "open"
        return run_id, answer(run_id, given, state_dir=state_dir)

    def _play_without_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
        run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
        raw_scpi(run_id, "psu0", "VOLT 5.0", state_dir=state_dir)
        given = "ok"
        try:
            raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=state_dir)
        except MeasurementFailed:
            given = "open"
        return run_id, answer(run_id, given, state_dir=state_dir)

    # Before the fix: the second of these MIN_RUNS calls to take_measurement
    # against the SAME driver_path raises CheckCouldNotRun("... claimed by 2
    # drivers ..."), and run_benchmark propagates it -- this call itself is
    # the reproduction. After the fix: every run finishes.
    result = run_benchmark(str(SAMPLE_TASK), play_with_shal=_play_with_shal,
                           play_without_shal=_play_without_shal, runs=MIN_RUNS,
                           state_dir=tmp_path / "state")
    assert result["ok"] is True
    assert result["with_shal"]["runs"] == MIN_RUNS
    assert result["without_shal"]["runs"] == MIN_RUNS
