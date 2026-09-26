"""Tests for the TI TMP102 reference driver, on the shal,sim-i2c sim bus.

Run from anywhere: this file puts its own folder on sys.path, so `driver` and
`sim` are the two files next to it. Copy the four files, rename, and keep going.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402,F401  (registers ti,tmp102)
import sim  # noqa: E402,F401     (registers the sim model)

import shal  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def _load():
    hal = shal.load(_TOPO)
    return hal, hal.get_device("tmp102"), hal.get_device("bench")


def test_read_celsius_is_the_model_value():
    hal, dev, bus = _load()
    with hal:
        assert dev.read_celsius() == pytest.approx(25.0, abs=0.0625)
        bus.model_for(0x48).temp_c = 31.5
        assert dev.read_celsius() == pytest.approx(31.5, abs=0.0625)


def test_is_a_temperature_sensor():
    hal, dev, _ = _load()
    with hal:
        assert isinstance(dev, shal.TemperatureSensor)


def test_idempotent_read_recovers_after_one_drop():
    hal, dev, bus = _load()
    with hal:
        bus.fail_next = 1
        assert dev.read_celsius() == pytest.approx(25.0, abs=0.0625)


def test_delivered_unknown_propagates():
    hal, dev, bus = _load()
    with hal:
        bus.fail_delivered_unknown = True
        with pytest.raises(shal.HopError) as exc:
            dev.read_celsius()
        assert exc.value.delivered == "unknown"


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.Tmp102, _TOPO)
    assert report.problems == []
    assert report.warnings == []
