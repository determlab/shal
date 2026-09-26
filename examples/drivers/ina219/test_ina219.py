"""ti,ina219 against the sim bus (hermetic, no hardware).

Moved from the core suite with the driver (#149). Run: pytest examples/drivers
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import ina219_driver  # noqa: E402,F401  (registers ti,ina219)
import ina219_sim  # noqa: E402,F401     (registers the sim model)

import shal  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def test_ina219_reads_voltage_current_power():
    with shal.load(_TOPO) as hal:
        m = hal.get_node("bench").driver.model_for(0x40)
        m.bus_v, m.current = 12.0, 0.5
        dev = hal.get_device("dev")
        assert dev.read_voltage() == pytest.approx(12.0, abs=0.01)
        assert dev.read_current() == pytest.approx(0.5, abs=0.001)
        assert dev.read_power() == pytest.approx(6.0, abs=0.05)


def test_ina219_is_a_power_monitor():
    with shal.load(_TOPO) as hal:
        assert isinstance(hal.get_device("dev"), shal.PowerMonitor)


def test_ina219_in_catalog():
    d = shal.catalog("ti,ina219")
    assert d["capability"] == "PowerMonitor"
    assert {o["name"] for o in d["ops"]} == {"read_voltage", "read_current", "read_power"}
    assert d["address_schema"]["examples"] == [64]
