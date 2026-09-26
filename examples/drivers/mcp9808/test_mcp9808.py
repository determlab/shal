"""microchip,mcp9808 against the sim bus (hermetic, no hardware).

Moved from the core suite with the driver (#149). Run: pytest examples/drivers
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import mcp9808_driver  # noqa: E402,F401  (registers microchip,mcp9808)
import mcp9808_sim  # noqa: E402,F401     (registers the sim model)
import shal  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def test_mcp9808_reads_temperature():
    with shal.load(_TOPO) as hal:
        hal.get_node("bench").driver.model_for(0x18).temp_c = 22.5
        assert hal.get_device("dev").read_celsius() == pytest.approx(22.5, abs=0.07)
        assert isinstance(hal.get_device("dev"), shal.TemperatureSensor)


def test_mcp9808_in_catalog():
    assert shal.catalog("microchip,mcp9808")["capability"] == "TemperatureSensor"
