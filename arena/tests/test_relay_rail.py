"""The `relay-rail` task (4 instruments, 3 protocols): a `scpi-psu` + `dmm`
pair (as `rail-3v3` already has), plus `relay0` (a Modbus-framed relay on
`shal,sim-msg`, switching the card's own power) and `temp0` (an
`sht31`-style temperature sensor on `shal,sim-i2c`, probing the regulator).

Covers: every fault (`ok`, `low_voltage`, `open`, `overheat`) is detectable;
`start_run`'s own output never leaks the hidden fault; the generic
`shal-arena call` path is gated (an actuator op refuses with no approver,
same as `shal call`) and logged to the sim log; switching the relay off
reads every rail at 0 V."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shal_arena import fault as fault_mod
from shal_arena.errors import CheckCouldNotRun, MeasurementFailed
from shal_arena.loader import load_task
from shal_arena.runner import answer, call_op, drive_input, start_run, take_measurement
from shal_arena.simlog import SimLog
from shal_arena.store import RunStore

from .conftest import (
    GATED_RELAY_DRIVER,
    PASSING_DMM_DRIVER,
    PASSING_DRIVER,
    PASSING_RELAY_DRIVER,
    PASSING_TEMP_DRIVER,
    RELAY_RAIL_TASK,
)

_LOADED = load_task(RELAY_RAIL_TASK)
_TASK, _CARD = _LOADED.task, _LOADED.card
_RAIL = next(r for r in _CARD.rails if r.test_point == "tp_3v3")
_TEMP = next(t for t in _CARD.temp_points if t.test_point == "tp_reg_temp")

# issue #451: `high_c` is now a PUBLIC field in every `start_run`'s own
# `temp_points` (the card's documented limit, same category as `rails`'
# already-public `nominal_v`/`tol_pct`) -- dropped from this list, since it
# never named the fault itself. `shift_c` (the fault's own internal
# amount) stays banned.
_FAULT_WORDS = ("low_voltage", "overheat", "shift_v", "shift_c")


def _seed_for(fault_id: str, limit: int = 500) -> int:
    for seed in range(limit):
        if fault_mod.realized_fault(_CARD, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


# --------------------------------------------------------------------------- #
# every fault is detectable from the probe instruments alone
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("fault_id", ["ok", "low_voltage", "open", "overheat"])
def test_each_fault_is_detectable_and_answerable(fault_id: str, tmp_path: Path) -> None:
    seed = _seed_for(fault_id)
    state_dir = tmp_path / fault_id
    result = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)
    run_id = result["run_id"]

    try:
        dmm_reading = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER,
                                       state_dir=state_dir)["reading"]
    except MeasurementFailed:
        assert fault_id == "open", f"seed {seed}: dmm0 failed for fault {fault_id!r}"
        dmm_reading = None

    temp_reading = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER,
                                    state_dir=state_dir)["reading"]

    if fault_id == "ok":
        assert dmm_reading == pytest.approx(_RAIL.nominal_v, abs=1e-6)
        assert temp_reading == pytest.approx(_TEMP.nominal_c, abs=0.01)
    elif fault_id == "low_voltage":
        assert dmm_reading != pytest.approx(_RAIL.nominal_v, abs=1e-6)
        assert temp_reading == pytest.approx(_TEMP.nominal_c, abs=0.01)
    elif fault_id == "open":
        assert dmm_reading is None
        assert temp_reading == pytest.approx(_TEMP.nominal_c, abs=0.01)
    else:  # overheat: temperature is high while the rail still reads nominal
        assert dmm_reading == pytest.approx(_RAIL.nominal_v, abs=1e-6)
        assert temp_reading > _TEMP.high_c

    outcome = answer(run_id, fault_id, state_dir=state_dir)
    assert outcome["correct"] is True
    assert outcome["disqualified"] is False


def test_overheat_is_indistinguishable_from_ok_on_the_rail_alone(tmp_path: Path) -> None:
    """The whole point of the fourth instrument: an agent reading only
    `dmm0` sees the exact same 'rail in spec' reading for `ok` and
    `overheat` — only `temp0` tells them apart."""
    ok_seed, hot_seed = _seed_for("ok"), _seed_for("overheat")
    readings = {}
    for label, seed in (("ok", ok_seed), ("overheat", hot_seed)):
        state_dir = tmp_path / label
        run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
        readings[label] = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER,
                                           state_dir=state_dir)["reading"]
    assert readings["ok"] == pytest.approx(readings["overheat"], abs=1e-6)


# --------------------------------------------------------------------------- #
# no answer leaks to the player
# --------------------------------------------------------------------------- #

def test_start_run_never_leaks_the_fault_or_its_vocabulary(tmp_path: Path) -> None:
    for fault_id in ("ok", "low_voltage", "open", "overheat"):
        seed = _seed_for(fault_id)
        state_dir = tmp_path / fault_id
        result = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)
        text = json.dumps(result)
        for word in _FAULT_WORDS:
            assert word not in text, (fault_id, word, text)


def test_run_state_file_never_names_the_fault_while_open(tmp_path: Path) -> None:
    seed = _seed_for("overheat")
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)

    for path in state_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(w in text for w in _FAULT_WORDS), (path, text)


# --------------------------------------------------------------------------- #
# the generic `call` path: gated, limited, and logged like any other turn
# --------------------------------------------------------------------------- #

def test_call_reads_and_writes_the_relay_through_shal(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("ok")
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]

    read = call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "read_relay", ["0"],
                   state_dir=state_dir)
    assert read["ok"] is True
    assert read["result"] is True  # channel 0 starts energized
    assert read["side_effect"] == "none"

    written = call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
                      state_dir=state_dir)
    assert written["ok"] is True
    assert written["side_effect"] == "write"

    read_again = call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "read_relay", ["0"],
                         state_dir=state_dir)
    assert read_again["result"] is False

    state = RunStore(state_dir).load(run_id)
    assert state.turns == 3  # every `call` is one turn, same as check/measure/drive

    entries = SimLog(RunStore(state_dir).sim_log_path(run_id)).entries()
    calls = [e for e in entries if e["kind"] == "call" and e["address"] == "relay0"]
    assert [c["op"] for c in calls] == ["read_relay", "set_relay", "read_relay"]
    assert all(c["ok"] for c in calls)


def test_call_on_an_unknown_op_names_the_fix(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]
    with pytest.raises(CheckCouldNotRun):
        call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "no_such_op", [], state_dir=state_dir)


def test_call_refuses_an_actuator_op_with_no_approver(tmp_path: Path) -> None:
    """The call path is the real `hal.call_tool` gate (AGENTS.md), not a
    second arena-local approval mechanism: an op the driver marks
    `side_effect="actuator"` stops for approval exactly like `shal call`
    would, and nothing is sent — while a `side_effect="write"` op on the
    very same instrument (the packaged relay driver) just runs."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]

    refused = call_op(run_id, "relay0", GATED_RELAY_DRIVER, "force_relay", ["0", "true"],
                      state_dir=state_dir)
    assert refused["ok"] is False
    assert refused["rejected"] == "approval"

    # nothing was sent: the coil this gated op would have written is unchanged
    read = call_op(run_id, "relay0", GATED_RELAY_DRIVER, "read_relay", ["0"],
                   state_dir=state_dir)
    assert read["result"] is True  # still the untouched default

    entries = SimLog(RunStore(state_dir).sim_log_path(run_id)).entries()
    logged = [e for e in entries if e["kind"] == "call" and e["op"] == "force_relay"]
    assert logged and logged[0]["ok"] is False


def test_call_enforces_the_ops_own_limits(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]

    result = call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["99", "true"],
                     state_dir=state_dir)
    assert result["ok"] is False
    assert result["rejected"] == "limits"


# --------------------------------------------------------------------------- #
# relay off cuts the card's own power: every rail reads 0 V
# --------------------------------------------------------------------------- #

def test_relay_off_gives_zero_volts_and_back_on_restores_it(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("ok")
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]

    before = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert before["reading"] == pytest.approx(_RAIL.nominal_v, abs=1e-6)

    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
           state_dir=state_dir)

    off = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert off["reading"] == pytest.approx(0.0, abs=1e-9)
    assert off["card"]["state"] == "ok"  # power off is not a damage state

    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "true"],
           state_dir=state_dir)
    back_on = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert back_on["reading"] == pytest.approx(_RAIL.nominal_v, abs=1e-6)


def test_relay_power_state_persists_across_separate_calls(tmp_path: Path) -> None:
    """Separate `call_op` invocations are separate CLI calls in real use
    (each one re-imports the driver file) — the relay's off state must
    survive between them, the same way `drive_input`'s applied voltage
    does (issue #313)."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=_seed_for("ok"),
                       state_dir=state_dir)["run_id"]
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
           state_dir=state_dir)

    assert RunStore(state_dir).load(run_id).card_power_on is False

    off = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert off["reading"] == pytest.approx(0.0, abs=1e-9)


def test_regulator_temperature_cools_to_ambient_with_power_off(tmp_path: Path) -> None:
    """CTO review (PR #426): a de-energized regulator is not dissipating
    power any more, hidden fault or not -- `temp0` must read room
    temperature, not whatever the realized fault would otherwise give it."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=_seed_for("overheat"),
                       state_dir=state_dir)["run_id"]
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
           state_dir=state_dir)

    off = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)
    assert off["reading"] < _TEMP.high_c
    assert off["reading"] == pytest.approx(25.0, abs=1.0)


# --------------------------------------------------------------------------- #
# CTO review (PR #426): a relay is not a voltage source, and `call` must not
# let a `drives` instrument bypass drive_input's own damage gate.
# --------------------------------------------------------------------------- #

def test_drive_refuses_the_relay_power_switch(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]

    with pytest.raises(CheckCouldNotRun):
        drive_input(run_id, "relay0", 13.5, state_dir=state_dir)

    # nothing was applied: the card's own input is untouched
    state = RunStore(state_dir).load(run_id)
    assert state.card_applied == {}


def test_call_refuses_a_voltage_write_on_the_psu_drives_instrument(tmp_path: Path) -> None:
    """Only `drive` carries the card's damage gate for a `drives`
    instrument (`_shal_damage_gate`) -- `call` reaching `set_voltage`
    directly would let a player set 20 V on `vin` with no check against
    the card's documented abs max at all."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]

    with pytest.raises(CheckCouldNotRun) as excinfo:
        call_op(run_id, "psu0", PASSING_DRIVER, "set_voltage", ["20"], state_dir=state_dir)
    assert "drive" in excinfo.value.fix

    # nothing was applied: the card's own input is untouched
    state = RunStore(state_dir).load(run_id)
    assert state.card_applied == {}


def test_call_still_allows_a_read_on_a_drives_instrument(tmp_path: Path) -> None:
    """The new guard is scoped to output changes -- reading `psu0`'s own
    measured voltage through `call` is harmless and still works."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=state_dir)["run_id"]

    result = call_op(run_id, "psu0", PASSING_DRIVER, "measure_voltage", [],
                     state_dir=state_dir)
    assert result["ok"] is True


def test_card_description_states_the_regulator_limit_and_rail_tolerance() -> None:
    """CTO review (PR #426): the player must be able to tell `overheat`
    from `ok` without guessing -- the thermal limit and the rail
    tolerance belong in the card description (or the question), not left
    implicit."""
    description = _CARD.description
    assert "85" in description  # the regulator's thermal limit
    assert "45" in description  # its nominal temperature
    assert "3%" in description or "3.3" in description  # the rail's own spec
