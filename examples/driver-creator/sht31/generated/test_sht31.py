"""Tests for the Sensirion SHT31-DIS driver, on the shal,sim-i2c sim bus.

Value vectors are the datasheet's worked examples (section 6). The retry path
is exercised via the sim bus hooks (fail_next / fail_delivered_unknown).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import shal  # noqa: E402
import driver  # noqa: E402,F401  (registers sensirion,sht31)
import sim  # noqa: E402,F401     (registers the sim model)
from driver import HumiditySensor  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def _load():
    hal = shal.load(_TOPO)
    dev = hal.get_device("sht31")
    bus = hal.get_device("bench")
    model = bus.model_for(0x44)
    return hal, dev, bus, model


# --- value correctness: datasheet worked examples (section 6) ---


def test_read_celsius_example1():
    # Example 1: S_T = 0x6666 -> T = 25.0 degC (exact).
    _, dev, _, model = _load()
    model.s_t = 0x6666
    assert dev.read_celsius() == pytest.approx(25.0, abs=1e-9)


def test_read_celsius_example2():
    # Example 2: S_T = 0x851E -> T = 45.99946593423361 degC.
    _, dev, _, model = _load()
    model.s_t = 0x851E
    assert dev.read_celsius() == pytest.approx(45.99946593423361, abs=1e-9)


def test_read_humidity_example3():
    # Example 3: S_RH = 0x8000 -> RH = 50.000762951094835 %RH.
    _, dev, _, model = _load()
    model.s_rh = 0x8000
    assert dev.read_humidity_percent() == pytest.approx(50.000762951094835, abs=1e-9)


def test_read_humidity_example4():
    # Example 4: S_RH = 0x3333 -> RH = 20.0 %RH (exact).
    _, dev, _, model = _load()
    model.s_rh = 0x3333
    assert dev.read_humidity_percent() == pytest.approx(20.0, abs=1e-9)


def test_complete_frame_example5():
    # Example 5: T = 25.0 degC, RH ~= 50.0008 %RH from one frame.
    _, dev, _, model = _load()
    model.s_t = 0x6666
    model.s_rh = 0x8000
    assert dev.read_celsius() == pytest.approx(25.0, abs=1e-9)
    assert dev.read_humidity_percent() == pytest.approx(50.000762951094835, abs=1e-9)


# --- capability protocols ---


def test_isinstance_temperature_sensor():
    _, dev, _, _ = _load()
    assert isinstance(dev, shal.TemperatureSensor)


def test_isinstance_humidity_sensor():
    _, dev, _, _ = _load()
    assert isinstance(dev, HumiditySensor)


# --- retry contract ---


def test_idempotent_read_recovers_after_one_drop():
    # An idempotent read survives one dropped delivery (framework retries once).
    _, dev, bus, model = _load()
    model.s_t = 0x6666
    bus.fail_next = 1
    assert dev.read_celsius() == pytest.approx(25.0, abs=1e-9)


def test_humidity_read_recovers_after_one_drop():
    _, dev, bus, model = _load()
    model.s_rh = 0x3333
    bus.fail_next = 1
    assert dev.read_humidity_percent() == pytest.approx(20.0, abs=1e-9)


def test_delivered_unknown_propagates():
    # A delivery-unknown failure is surfaced, not retried.
    _, dev, bus, _ = _load()
    bus.fail_delivered_unknown = True
    with pytest.raises(shal.HopError) as exc:
        dev.read_celsius()
    assert exc.value.delivered == "unknown"
