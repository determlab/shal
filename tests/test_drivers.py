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


def test_mcp9808_reads_temperature(tmp_path):
    with _load(tmp_path, "0x18", "microchip,mcp9808") as hal:
        hal.get_node("bench").driver.model_for(0x18).temp_c = 22.5
        assert hal.get_device("dev").read_celsius() == pytest.approx(22.5, abs=0.07)
        assert isinstance(hal.get_device("dev"), shal.TemperatureSensor)


def test_ads1115_reads_channels(tmp_path):
    with _load(tmp_path, "0x48", "ti,ads1115") as hal:
        m = hal.get_node("bench").driver.model_for(0x48)
        m.voltages = {0: 1.0, 1: 2.0, 3: -1.0}
        dev = hal.get_device("dev")
        assert dev.read_voltage(0) == pytest.approx(1.0, abs=0.002)
        assert dev.read_voltage(1) == pytest.approx(2.0, abs=0.002)
        assert dev.read_voltage(3) == pytest.approx(-1.0, abs=0.002)
        assert isinstance(dev, shal.ADC)


def test_mcp23017_gpio_roundtrip(tmp_path):
    with _load(tmp_path, "0x20", "microchip,mcp23017") as hal:
        dev = hal.get_device("dev")
        dev.set_direction(0, output=True)
        dev.write_pin(0, high=True)
        assert dev.read_pin(0) is True
        dev.write_pin(0, high=False)
        assert dev.read_pin(0) is False
        assert isinstance(dev, shal.GPIOExpander)


def test_new_drivers_in_catalog():
    assert shal.catalog("microchip,mcp9808")["capability"] == "TemperatureSensor"
    assert shal.catalog("ti,ads1115")["capability"] == "ADC"
    assert shal.catalog("microchip,mcp23017")["capability"] == "GPIOExpander"
