"""tests/test_sim_unplug.py — `fault: unplugged` / `SHAL_SIM_UNPLUG` / `after: N`
on every sim bus, not only `shal,sim-scpi` (issue #349).

Cold audit: `SHAL_SIM_UNPLUG` and `fault: unplugged` (#304) worked only on
`shal,sim-scpi`. The README's own `sim.yaml` first-success example uses
`shal,sim-i2c`, so the documented unplug silently did nothing there. This file
proves the fault now works the same way on both buses: a node with `fault:
unplugged`, or named by `SHAL_SIM_UNPLUG`, refuses every hop with
`HopError(delivered="no")` and its address in the message; an unrelated device
on the same bus is unaffected; and the optional `after: N` node key lets the
device answer the first N calls normally before it drops, so a demo can pull
the cable mid-run — the step that then errors records `cause: "transport"`
(#301).
"""
from __future__ import annotations

import textwrap

import pytest

import shal
from shal.errors import HopError
from shal.record import Step


def write(tmp_path, body: str):
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_I2C_TOPO = """
    shal_version: 1
    root:
      bus:
        driver: shal,sim-i2c
        address: sim0
        children:
          temp0:
            id: ambient_temp
            driver: shal,sim-sensor
            address: 0x48
            {fault_line}
            {after_line}
          temp1:
            id: ambient_temp_1
            driver: shal,sim-sensor
            address: 0x49
"""

_SCPI_TOPO = """
    shal_version: 1
    root:
      bench:
        driver: shal,sim-scpi
        address: sim0
        children:
          psu0:
            id: psu0
            driver: shal,sim-psu
            address: psu0
            {fault_line}
            {after_line}
          psu1:
            id: psu1
            driver: shal,sim-psu
            address: psu1
"""


class _I2cCase:
    """`shal,sim-i2c` — the README's own first-success bus."""

    topo = _I2C_TOPO
    faulted_id = "ambient_temp"
    other_id = "ambient_temp_1"
    addr_text = "0x48"

    @staticmethod
    def call(hal, dev_id):
        return hal.get_device(dev_id).read_celsius()


class _ScpiCase:
    topo = _SCPI_TOPO
    faulted_id = "psu0"
    other_id = "psu1"
    addr_text = "psu0"

    @staticmethod
    def call(hal, dev_id):
        return hal.get_device(dev_id).measure_voltage()


CASES = [pytest.param(_I2cCase, id="sim-i2c"), pytest.param(_ScpiCase, id="sim-scpi")]


def _write_case(tmp_path, case, *, fault: bool, after: int | None = None):
    fault_line = "fault: unplugged" if fault else ""
    after_line = f"after: {after}" if after is not None else ""
    return write(tmp_path, case.topo.format(fault_line=fault_line, after_line=after_line))


# ---- `fault: unplugged` in the topology file, on both buses -----------------------

@pytest.mark.parametrize("case", CASES)
def test_fault_unplugged_raises_hop_error_with_address_in_message(tmp_path, case):
    p = _write_case(tmp_path, case, fault=True)
    with shal.load(p) as hal:
        with pytest.raises(HopError) as ei:
            case.call(hal, case.faulted_id)
        assert ei.value.delivered == "no"
        assert case.addr_text in str(ei.value)
        # the other device on the same bus, no fault: unaffected
        case.call(hal, case.other_id)


@pytest.mark.parametrize("case", CASES)
def test_no_fault_and_no_env_var_behaves_as_today(tmp_path, case):
    p = _write_case(tmp_path, case, fault=False)
    with shal.load(p) as hal:
        case.call(hal, case.faulted_id)


# ---- `SHAL_SIM_UNPLUG=<node id>` does the same, without editing the file -----------

@pytest.mark.parametrize("case", CASES)
def test_shal_sim_unplug_env_var_raises_hop_error_with_address_in_message(
        tmp_path, monkeypatch, case):
    p = _write_case(tmp_path, case, fault=False)
    monkeypatch.setenv("SHAL_SIM_UNPLUG", case.faulted_id)
    with shal.load(p) as hal:
        with pytest.raises(HopError) as ei:
            case.call(hal, case.faulted_id)
        assert ei.value.delivered == "no"
        assert case.addr_text in str(ei.value)
        # the OTHER device on the same bus, not named by the env var, still answers
        case.call(hal, case.other_id)


# ---- `after: N` — N calls answer, then it drops (#349) -----------------------------

@pytest.mark.parametrize("case", CASES)
def test_after_n_answers_n_calls_then_drops(tmp_path, case):
    p = _write_case(tmp_path, case, fault=True, after=2)
    with shal.load(p) as hal:
        case.call(hal, case.faulted_id)   # 1st call: answers
        case.call(hal, case.faulted_id)   # 2nd call: answers
        with pytest.raises(HopError) as ei:
            case.call(hal, case.faulted_id)   # 3rd call: drops
        assert ei.value.delivered == "no"
        assert case.addr_text in str(ei.value)


@pytest.mark.parametrize("case", CASES)
def test_after_n_the_dropped_call_records_cause_transport(tmp_path, case):
    p = _write_case(tmp_path, case, fault=True, after=2)
    with shal.load(p) as hal:
        case.call(hal, case.faulted_id)
        case.call(hal, case.faulted_id)
        with pytest.raises(HopError) as ei:
            case.call(hal, case.faulted_id)
        step = Step.from_error("call", ei.value)
        assert step.cause == "transport"
        assert step.verdict == "error"


@pytest.mark.parametrize("case", CASES)
def test_shal_sim_unplug_env_var_with_after_n_also_answers_n_then_drops(
        tmp_path, monkeypatch, case):
    p = _write_case(tmp_path, case, fault=False, after=1)
    monkeypatch.setenv("SHAL_SIM_UNPLUG", case.faulted_id)
    with shal.load(p) as hal:
        case.call(hal, case.faulted_id)   # 1st call: answers
        with pytest.raises(HopError):
            case.call(hal, case.faulted_id)   # 2nd call: drops
