"""rigol,dp832 — set_voltage limits come from the DP832 ratings (#150).

CH1/CH2 are 0-30 V, CH3 is 0-5 V. A value above the channel's rating is a
LimitError raised by the framework BEFORE the op body, so the bus records no
write at all."""
import textwrap

import pytest

import shal
from shal.buses.sim_scpi import SimScpiBus
from shal.conformance import check_driver
from shal.drivers.rigol_dp832 import RigolDp832

RACK_YAML = """
    shal_version: 1
    root:
      bench:
        id: bench
        driver: shal,sim-scpi
        address: sim0
        children:
          ch1: {id: ch1, driver: "rigol,dp832", address: 1}
          ch2: {id: ch2, driver: "rigol,dp832", address: 2}
          ch3: {id: ch3, driver: "rigol,dp832", address: 3}
"""


@pytest.fixture
def rack(tmp_path, monkeypatch):
    """The loaded rack plus a list of every exchange() the sim bus receives."""
    calls: list = []
    real = SimScpiBus.exchange

    def spy(self, addr, payload):
        calls.append((addr, payload))
        return real(self, addr, payload)

    monkeypatch.setattr(SimScpiBus, "exchange", spy)
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(RACK_YAML), encoding="utf-8")
    with shal.load(p) as hal:
        yield hal, calls


@pytest.mark.parametrize("dev, volts", [
    ("ch1", 31.0),    # above the CH1 rating (30 V)
    ("ch2", 30.5),    # above the CH2 rating (30 V)
    ("ch3", 6.0),     # legal on CH1/CH2, above the CH3 rating (5 V)
    ("ch1", -1.0),    # below 0 V
])
def test_set_voltage_above_rating_fails_before_any_bus_call(rack, dev, volts):
    hal, calls = rack
    with pytest.raises(shal.LimitError) as ei:
        hal.get_device(dev).set_voltage(volts)
    assert calls == []                            # nothing reached the bus
    assert ei.value.violations[0]["param"] == "volts"


@pytest.mark.parametrize("dev, volts, cmd", [
    ("ch1", 30.0, ":SOUR1:VOLT 30.0"),
    ("ch3", 5.0, ":SOUR3:VOLT 5.0"),
])
def test_set_voltage_at_rating_reaches_the_bus(rack, dev, volts, cmd):
    hal, calls = rack
    hal.get_device(dev).set_voltage(volts)
    assert [payload["scpi"] for _, payload in calls] == [cmd]   # the spy sees writes


def test_tool_schema_advertises_the_per_channel_rating(rack):
    hal, _ = rack
    tools = {t["name"]: t for t in hal.tool_schemas()}
    for dev, maximum in (("ch1", 30.0), ("ch2", 30.0), ("ch3", 5.0)):
        volts = tools[f"{dev}__set_voltage"]["input_schema"]["properties"]["volts"]
        assert volts["minimum"] == 0.0 and volts["maximum"] == maximum


def test_check_driver_reports_no_problems_and_no_warnings():
    report = check_driver(RigolDp832)
    assert report.problems == []
    assert report.warnings == []
