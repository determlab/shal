"""ti,ads1115 against the sim bus (hermetic, no hardware).

Moved from the core suite with the driver (#149). Run: pytest examples/drivers
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import ads1115_driver  # noqa: E402,F401  (registers ti,ads1115)
import ads1115_sim  # noqa: E402,F401     (registers the sim model)

import shal  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def test_ads1115_reads_channels():
    with shal.load(_TOPO) as hal:
        m = hal.get_node("bench").driver.model_for(0x48)
        m.voltages = {0: 1.0, 1: 2.0, 3: -1.0}
        dev = hal.get_device("dev")
        assert dev.read_voltage(0) == pytest.approx(1.0, abs=0.002)
        assert dev.read_voltage(1) == pytest.approx(2.0, abs=0.002)
        assert dev.read_voltage(3) == pytest.approx(-1.0, abs=0.002)
        assert isinstance(dev, shal.ADC)


def test_ads1115_in_catalog():
    assert shal.catalog("ti,ads1115")["capability"] == "ADC"
