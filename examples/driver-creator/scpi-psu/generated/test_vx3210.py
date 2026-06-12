"""Tests for the Vexar VX3210 driver, run entirely against the sim model.

Value vectors come from the manual's worked session (§5/§6): with the output ON
and a load drawing 842 mA at 12.5 V, MEAS:VOLT? -> 12.500 and MEAS:CURR? ->
0.842; with the output OFF both read 0.000.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import shal
import driver  # noqa: F401  (registers vexar,vx3210)
import sim     # noqa: F401  (registers the sim model)

TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def hal():
    h = shal.load(TOPO)
    yield h
    h.close()


@pytest.fixture
def psu(hal):
    return hal.get_device("psu")


@pytest.fixture
def bus(hal):
    return hal.get_device("bench")


# -- capability -------------------------------------------------------------

def test_implements_power_supply(psu):
    assert isinstance(psu, shal.PowerSupply)


# -- value correctness (worked examples §5/§6) ------------------------------

def test_setpoint_reads_back(psu, bus):
    psu.set_voltage(12.5)
    psu.set_current_limit(2.0)
    model = bus.model_for("192.168.1.50:5025")
    assert model.volt_set == pytest.approx(12.5)
    assert model.curr_set == pytest.approx(2.0)


def test_measure_with_output_off_reads_zero(psu):
    psu.set_voltage(12.5)
    psu.output(False)
    assert psu.read_voltage() == pytest.approx(0.0)
    assert psu.read_current() == pytest.approx(0.0)


def test_measure_with_output_on(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    model.load_amps = 0.842
    psu.set_voltage(12.5)
    psu.output(True)
    assert psu.read_voltage() == pytest.approx(12.5)   # §5: MEAS:VOLT? -> 12.500
    assert psu.read_current() == pytest.approx(0.842)  # §5: MEAS:CURR? -> 0.842


def test_output_toggles_state(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    psu.output(True)
    assert model.output_on is True
    psu.output(False)
    assert model.output_on is False


# -- retry contract (SDK §5) ------------------------------------------------

def test_idempotent_read_recovers_after_one_drop(psu, bus):
    psu.set_voltage(12.5)
    psu.output(True)
    bus.fail_next = 1
    # read_voltage is @idempotent → framework retries once on delivered="no"
    assert psu.read_voltage() == pytest.approx(12.5)


def test_delivered_unknown_propagates(psu, bus):
    bus.fail_delivered_unknown = True
    with pytest.raises(shal.HopError) as ei:
        psu.read_voltage()
    assert ei.value.delivered == "unknown"


# -- limit rejection: one per declared bound (sim state unchanged) ----------

def test_voltage_over_max_rejected(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    before = model.volt_set
    with pytest.raises(shal.LimitError):
        psu.set_voltage(48.0)        # > 32.0 V max
    assert model.volt_set == before  # nothing reached the instrument


def test_voltage_under_min_rejected(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    before = model.volt_set
    with pytest.raises(shal.LimitError):
        psu.set_voltage(-1.0)        # < 0.0 V min
    assert model.volt_set == before


def test_current_limit_over_max_rejected(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    before = model.curr_set
    with pytest.raises(shal.LimitError):
        psu.set_current_limit(6.0)   # > 5.0 A max
    assert model.curr_set == before


def test_current_limit_under_min_rejected(psu, bus):
    model = bus.model_for("192.168.1.50:5025")
    before = model.curr_set
    with pytest.raises(shal.LimitError):
        psu.set_current_limit(-0.5)  # < 0.0 A min
    assert model.curr_set == before


# -- conformance ------------------------------------------------------------

def test_conformance():
    from shal import conformance
    report = conformance.check_driver("vexar,vx3210", topology=TOPO)
    assert report.ok, str(report)
