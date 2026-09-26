"""rigol,dp832 — set_voltage limits come from the DP832 ratings (#150).

CH1/CH2 are 0-30 V, CH3 is 0-5 V. A value above the channel's rating is a
LimitError raised by the framework BEFORE the op body, so the bus records no
write at all.

Run from anywhere: this file puts its own folder on sys.path, so `driver` and
`sim` are the two files next to it."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import sim  # noqa: E402,F401     (registers the sim model)
from driver import RigolDp832  # noqa: E402  (registers rigol,dp832)

import shal  # noqa: E402
from shal.buses.sim_scpi import SimScpiBus  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def rack(monkeypatch):
    """The loaded rack plus a list of every exchange() the sim bus receives."""
    calls: list = []
    real = SimScpiBus.exchange

    def spy(self, addr, payload):
        calls.append((addr, payload))
        return real(self, addr, payload)

    monkeypatch.setattr(SimScpiBus, "exchange", spy)
    with shal.load(_TOPO) as hal:
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
    report = check_driver(RigolDp832, _TOPO)
    assert report.problems == []
    assert report.warnings == []
