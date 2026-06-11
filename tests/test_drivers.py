"""Bundled device drivers against the sim bus (hermetic, no hardware)."""
import pytest

import shal


def _load(tmp_path, addr_hex: str, compatible: str):
    p = tmp_path / "s.yaml"
    p.write_text(
        "shal_version: 1\n"
        "root:\n"
        "  bench:\n"
        "    id: bench\n"
        "    driver: shal,sim-i2c\n"
        "    address: sim0\n"
        "    children:\n"
        f"      d: {{id: dev, driver: '{compatible}', address: {addr_hex}}}\n",
        encoding="utf-8")
    return shal.load(p)


def test_ina219_reads_voltage_current_power(tmp_path):
    with _load(tmp_path, "0x40", "ti,ina219") as hal:
        m = hal.get_node("bench").driver.model_for(0x40)
        m.bus_v, m.current = 12.0, 0.5
        dev = hal.get_device("dev")
        assert dev.read_voltage() == pytest.approx(12.0, abs=0.01)
        assert dev.read_current() == pytest.approx(0.5, abs=0.001)
        assert dev.read_power() == pytest.approx(6.0, abs=0.05)


def test_ina219_is_a_power_monitor(tmp_path):
    with _load(tmp_path, "0x40", "ti,ina219") as hal:
        assert isinstance(hal.get_device("dev"), shal.PowerMonitor)


def test_ina219_in_catalog():
    d = shal.catalog("ti,ina219")
    assert d["capability"] == "PowerMonitor"
    assert {o["name"] for o in d["ops"]} == {"read_voltage", "read_current", "read_power"}
    assert d["address_schema"]["examples"] == [64]
