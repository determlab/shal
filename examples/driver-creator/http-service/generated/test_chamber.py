"""Tests for the Lumen ChamberLink CL-340 driver + sim.

Covers (per shal-generate-driver step 4 / SDK guide §6-7):
  - one value test per op using the docs' worked-example vectors;
  - retry behavior (fail_next recovers idempotent read; fail_delivered_unknown
    surfaces delivered="unknown" on the non-idempotent actuator op);
  - one LimitError test per declared bound (state unchanged);
  - isinstance against the TemperatureSensor capability protocol;
  - conformance.check_driver is green.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import shal  # noqa: E402
from shal.errors import HopError  # noqa: E402

import driver  # noqa: E402,F401  (registers lumen,chamber-api)
import sim  # noqa: E402,F401  (registers the sim model)

TOPOLOGY = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def chamber():
    hal = shal.load(TOPOLOGY)
    dev = hal.get_device("chamber")
    yield dev
    hal.close()


# ---- worked-example value vectors (docs notes table) ------------------------

def test_read_status_vector(chamber):
    # Example #1: chamber soaking at 65 degC, running, door closed.
    model = chamber.bus.model_for(chamber.addr)
    model.setpoint_c = 65.0
    model.running = True
    model.door_open = False
    model._settle()  # converged -> temp_c == setpoint_c
    assert chamber.read_status() == {
        "temp_c": 65.0,
        "setpoint_c": 65.0,
        "door_open": False,
        "running": True,
    }


def test_read_celsius_vector(chamber):
    # Example #1: temp_c == 65.0 in that scenario.
    model = chamber.bus.model_for(chamber.addr)
    model.setpoint_c = 65.0
    model.running = True
    model._settle()
    assert chamber.read_celsius() == 65.0


def test_set_temperature_vector(chamber):
    # Example #2: set_temperature 85.5 -> setpoint echoed; readback confirms.
    chamber.set_temperature(85.5)
    assert chamber.read_status()["setpoint_c"] == 85.5


def test_start_vector(chamber):
    # Example #3: start -> running true.
    chamber.start()
    assert chamber.read_status()["running"] is True


def test_stop_vector(chamber):
    # Example #4: stop -> running false.
    chamber.start()
    chamber.stop()
    assert chamber.read_status()["running"] is False


# ---- capability protocol ----------------------------------------------------

def test_isinstance_temperature_sensor(chamber):
    assert isinstance(chamber, shal.TemperatureSensor)


# ---- retry contract ---------------------------------------------------------

def test_idempotent_read_recovers_after_fail_next(chamber):
    model = chamber.bus.model_for(chamber.addr)
    model.setpoint_c = 65.0
    model.running = True
    model._settle()
    chamber.bus.fail_next = 1  # next call dropped delivered="no"; framework retries
    assert chamber.read_celsius() == 65.0


def test_non_idempotent_actuator_surfaces_unknown(chamber):
    # start is NOT @idempotent: a delivery-unknown failure must reach the user
    # untouched (no silent re-fire).
    chamber.bus.fail_delivered_unknown = True
    with pytest.raises(HopError) as ei:
        chamber.start()
    assert ei.value.delivered == "unknown"


# ---- declared limits (one per bound) ----------------------------------------

def test_limit_rejects_above_max(chamber):
    before = chamber.read_status()
    with pytest.raises(shal.LimitError):
        chamber.set_temperature(180.0001)
    # Pre-I/O rejection: chamber state unchanged.
    assert chamber.read_status() == before


def test_limit_rejects_below_min(chamber):
    before = chamber.read_status()
    with pytest.raises(shal.LimitError):
        chamber.set_temperature(-40.0001)
    assert chamber.read_status() == before


def test_limit_accepts_boundaries(chamber):
    chamber.set_temperature(-40.0)
    assert chamber.read_status()["setpoint_c"] == -40.0
    chamber.set_temperature(180.0)
    assert chamber.read_status()["setpoint_c"] == 180.0


# ---- device-said-no is not a transport error --------------------------------

def test_device_refusal_raises_chambererror(chamber):
    # Open the interlock; start is refused at the device level (ok=false).
    model = chamber.bus.model_for(chamber.addr)
    model.door_open = True
    with pytest.raises(driver.ChamberError):
        chamber.start()


# ---- conformance ------------------------------------------------------------

def test_conformance():
    from shal import conformance
    report = conformance.check_driver("lumen,chamber-api", topology=TOPOLOGY)
    assert report.ok, str(report)
