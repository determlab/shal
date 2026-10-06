"""issue #396: the two minimal driver examples named in arena/README.md's
"Write your driver" section run exactly as the README shows -- checked, then
played for a real reading -- so a cold agent copying them gets working code.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from shal import registry

from shal_arena.runner import check_instrument_driver, start_run, take_measurement

from .conftest import SAMPLE_TASK

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MINIMAL_PSU_DRIVER = _EXAMPLES_DIR / "minimal_psu_driver.py"
MINIMAL_DMM_DRIVER = _EXAMPLES_DIR / "minimal_dmm_driver.py"


@pytest.fixture(autouse=True)
def _clean_registry_slots():
    """Neither minimal example uses override=True (arena#394 made that
    unnecessary) -- start each test from a clean slot for both compatibles,
    same reasoning as test_bench_driver_once.py's own fixture."""
    saved = {c: list(registry._entries.get(c, [])) for c in
             ("arena,bench-psu1", "arena,bench-dmm1")}
    for c in saved:
        registry._entries[c] = []
    try:
        yield
    finally:
        for c, candidates in saved.items():
            registry._entries[c] = candidates


def test_minimal_psu_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "psu0", MINIMAL_PSU_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_dmm_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "dmm0", MINIMAL_DMM_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_dmm_driver_example_takes_a_real_reading(tmp_path: Path) -> None:
    # A SEPARATE test (its own fresh registry slot, via the autouse fixture
    # above): calling check_instrument_driver and take_measurement for the
    # SAME instrument in the same process re-imports the SAME driver.py
    # twice outside of a bench run, which still "claims by 2 drivers" --
    # that general case is a separate bug from #394 (bench-loop specific)
    # and explicitly out of scope here ("Out of scope: bench code changes").
    # The README's own step 3 avoids it by checking one instrument and
    # measuring a DIFFERENT one, same as the existing "Agent path" section.
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    # seed 1's realized fault for rail-3v3.yaml's card is not `open` (README's
    # own walkthrough picks seed 1 for exactly this reason) -- so the probe
    # instrument is reachable, and this is a real reading, not just a check.
    result = take_measurement(run_id, "dmm0", MINIMAL_DMM_DRIVER, state_dir=tmp_path)
    assert isinstance(result["reading"], float)


def test_examples_are_named_in_the_readme() -> None:
    readme = (_EXAMPLES_DIR.parent / "README.md").read_text(encoding="utf-8")
    assert "Write your driver" in readme
    assert "examples/minimal_psu_driver.py" in readme
    assert "examples/minimal_dmm_driver.py" in readme
