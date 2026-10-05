"""issue #312 Scope: "fault injection at run time from seed" actually changes
what the probing instrument measures — for the `dmm` case wired to a card
test point, via the in-memory harness `fault.harness_for_run` generates.
Complements test_fault_seed.py (the fault/noise pick itself) and
test_scoring.py (disqualification, the score file)."""
from __future__ import annotations

import runpy

import pytest
from shal import load
from shal.errors import HopError

from shal_arena import fault as fault_mod
from shal_arena.cases import resolve_case
from shal_arena.loader import load_task

from .conftest import FIXTURES, SAMPLE_TASK

PASSING_DMM_DRIVER = FIXTURES / "drivers" / "passing_dmm_driver.py"
runpy.run_path(str(PASSING_DMM_DRIVER))  # registers "arena,bench-dmm1" once for this module

_CARD = load_task(SAMPLE_TASK).card
_RAIL = next(r for r in _CARD.rails if r.test_point == "tp_3v3")
_CASE = resolve_case("dmm")


def _seed_for(fault_id: str, limit: int = 300) -> int:
    for seed in range(limit):
        if fault_mod.realized_fault(_CARD, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


def _measure_once(topology) -> float:
    with load(topology) as hal:
        return hal.call_tool("unit__measure_voltage", {})["result"]


def test_ok_reads_the_rails_nominal_voltage() -> None:
    # 'ok' never needs a generated topology at all (runner._topology_for_instrument
    # returns the case's static harness unchanged) — exercised here directly.
    assert _measure_once(str(_CASE.harness_topology)) == pytest.approx(_RAIL.nominal_v)


def test_low_voltage_shifts_the_reading_by_shift_v() -> None:
    seed = _seed_for("low_voltage")
    realized = fault_mod.realized_fault(_CARD, seed)
    topology = fault_mod.harness_for_run(_CASE, rail=_RAIL, realized=realized, seed=seed)
    reading = _measure_once(topology)
    assert reading == pytest.approx(_RAIL.nominal_v + realized.extra["shift_v"])


def test_noise_varies_the_reading_around_nominal() -> None:
    seed = _seed_for("noise")
    realized = fault_mod.realized_fault(_CARD, seed)
    topology = fault_mod.harness_for_run(_CASE, rail=_RAIL, realized=realized, seed=seed)
    ripple = realized.extra["ripple_vpp"]
    with load(topology) as hal:
        readings = [hal.call_tool("unit__measure_voltage", {})["result"] for _ in range(6)]
    assert len({round(r, 6) for r in readings}) > 1, (
        "noise should not read exactly the same value on every call")
    for r in readings:
        assert abs(r - _RAIL.nominal_v) <= ripple / 2 + 1e-9


def test_open_makes_the_instrument_unreachable() -> None:
    # direct driver call, not hal.call_tool: the tool-use surface catches and
    # reports a HopError rather than raising it, same as runner._take_measurement
    seed = _seed_for("open")
    realized = fault_mod.realized_fault(_CARD, seed)
    topology = fault_mod.harness_for_run(_CASE, rail=_RAIL, realized=realized, seed=seed)
    with pytest.raises(HopError), load(topology) as hal:
        node = next(n for root in hal._roots for n in root.walk() if n.id == "unit")
        node.driver.measure_voltage()


def test_same_seed_gives_the_same_reading() -> None:
    seed = _seed_for("low_voltage")
    realized = fault_mod.realized_fault(_CARD, seed)
    topology = fault_mod.harness_for_run(_CASE, rail=_RAIL, realized=realized, seed=seed)
    assert _measure_once(topology) == pytest.approx(_measure_once(topology))
