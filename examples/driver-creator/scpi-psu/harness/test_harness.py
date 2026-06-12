"""Independent validation harness for a GENERATED vexar,vx3210 driver.

Validates ../generated/driver.py against the referee's sim model
(sim_harness.py) and the worked examples in docs/vx3210-manual.md §5.
The generated driver's own sim model is deliberately overridden — passing
here means the driver matches the DOCUMENTATION, not its own sim.

Run from the repo root:  python -m pytest examples/driver-creator/scpi-psu/harness -q
Skips cleanly until generated/driver.py exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "generated"
pytestmark = pytest.mark.skipif(not (GEN / "driver.py").exists(),
                                reason="not generated yet")

COMPATIBLE = "vexar,vx3210"

if (GEN / "driver.py").exists():
    sys.path.insert(0, str(GEN))
    sys.path.insert(0, str(HERE))

    import shal
    from shal import conformance
    from shal.buses import sim_scpi

    import driver  # noqa: F401  (registers the generated driver via @shal.register)
    import sim_harness

    # Force the HARNESS model to win, whatever the generated code registered:
    sim_scpi.SCPI_SIM_MODELS[COMPATIBLE] = sim_harness.Vx3210HarnessModel


@pytest.fixture()
def hal():
    # Re-assert the override (cheap, idempotent) in case any import re-registered.
    sim_scpi.SCPI_SIM_MODELS[COMPATIBLE] = sim_harness.Vx3210HarnessModel
    h = shal.load(HERE / "topology.yaml")
    try:
        yield h
    finally:
        h.close()


def _model(h):
    """The referee's model instance behind address psu1."""
    m = h.get_node("rack").driver.model_for("psu1")
    assert isinstance(m, sim_harness.Vx3210HarnessModel), \
        "harness model did not win the registry override"
    return m


def _current_setter(dev):
    for name in ("set_current_limit", "set_current"):
        fn = getattr(dev, name, None)
        if callable(fn):
            return fn
    pytest.fail("generated driver exposes no current-limit setter "
                "(expected set_current_limit(amps: float))")


# ---- capability ---------------------------------------------------------------

def test_capability_is_power_supply(hal):
    dev = hal.get_device("dut")
    assert isinstance(dev, shal.PowerSupply), \
        "vexar,vx3210 must implement shal.PowerSupply"


# ---- worked-example vectors (manual §5) ----------------------------------------

def test_set_voltage_programs_setpoint(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    dev.set_voltage(12.5)                       # manual: "VOLT 12.500"
    assert m.voltage_setpoint == pytest.approx(12.5)
    assert m.scpi("VOLT?") == "12.500"          # doc vector, exact string


def test_read_voltage_tracks_setpoint_when_output_on(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    dev.set_voltage(12.5)
    dev.output(True)
    assert m.output_on is True
    assert dev.read_voltage() == pytest.approx(12.5)   # MEAS:VOLT? -> "12.500"


def test_read_voltage_is_zero_when_output_off(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    dev.set_voltage(12.5)
    dev.output(False)
    assert m.output_on is False
    assert dev.read_voltage() == pytest.approx(0.0)    # MEAS:VOLT? -> "0.000"


def test_read_current_reports_load_current(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    m.load_current = 0.842                      # manual §5: load drawing 842 mA
    dev.output(True)
    assert dev.read_current() == pytest.approx(0.842)  # MEAS:CURR? -> "0.842"
    dev.output(False)
    assert dev.read_current() == pytest.approx(0.0)    # "0.000" when OFF


def test_current_limit_setter_programs_setpoint(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    _current_setter(dev)(2.0)                   # manual: "CURR 2.000"
    assert m.current_limit == pytest.approx(2.0)
    assert m.scpi("CURR?") == "2.000"           # doc vector, exact string


def test_output_toggles_model_state(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    assert m.output_on is False                 # power-on default (manual §6)
    dev.output(True)
    assert m.output_on is True
    assert m.scpi("OUTP?") == "1"
    dev.output(False)
    assert m.output_on is False
    assert m.scpi("OUTP?") == "0"


def test_idn_vector_against_harness_model(hal):
    # Sanity-pins the harness model itself to the manual's worked example.
    assert _model(hal).scpi("*IDN?") == "VEXAR,VX3210,VX3-24117,1.07"


# ---- limit enforcement (manual §3 / §3.1) ---------------------------------------
# LimitError must fire BEFORE any I/O: the model state must be untouched.

@pytest.mark.parametrize("volts", [32.001, 100.0])
def test_voltage_above_max_rejected(hal, volts):
    dev, m = hal.get_device("dut"), _model(hal)
    dev.set_voltage(5.0)
    with pytest.raises(shal.LimitError):
        dev.set_voltage(volts)
    assert m.voltage_setpoint == pytest.approx(5.0), \
        "out-of-range setpoint reached the instrument"


def test_voltage_below_min_rejected(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    dev.set_voltage(5.0)
    with pytest.raises(shal.LimitError):
        dev.set_voltage(-0.5)
    assert m.voltage_setpoint == pytest.approx(5.0)


def test_current_above_max_rejected(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    setter = _current_setter(dev)
    setter(1.0)
    with pytest.raises(shal.LimitError):
        setter(5.5)
    assert m.current_limit == pytest.approx(1.0)


def test_current_below_min_rejected(hal):
    dev, m = hal.get_device("dut"), _model(hal)
    setter = _current_setter(dev)
    setter(1.0)
    with pytest.raises(shal.LimitError):
        setter(-1.0)
    assert m.current_limit == pytest.approx(1.0)


# ---- conformance: the definition of done ----------------------------------------

def test_conformance_report_ok():
    sim_scpi.SCPI_SIM_MODELS[COMPATIBLE] = sim_harness.Vx3210HarnessModel
    report = conformance.check_driver(COMPATIBLE, topology=HERE / "topology.yaml")
    assert report.ok, str(report)
