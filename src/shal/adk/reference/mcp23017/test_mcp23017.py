"""Tests for the Microchip MCP23017 reference driver, on the shal,sim-i2c sim bus.

Three labels on one device: read_pin is `none`, set_direction is `config`,
write_pin is `actuator`. The two gated ops need an approver; tests grant one.
Run from anywhere: this file puts its own folder on sys.path.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402,F401  (registers microchip,mcp23017)
import sim  # noqa: E402,F401     (registers the sim model)

import shal  # noqa: E402
from shal.conformance import check_driver  # noqa: E402
from shal.driver import inferred_side_effect  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def hal():
    with shal.load(_TOPO) as h:
        yield h


@pytest.fixture
def spy(hal):
    """The sim model plus a list of every transaction that reaches it."""
    model = hal.get_node("bench").driver.model_for(0x20)
    txns: list = []
    real_txn = model.txn
    model.txn = lambda ops: txns.append(ops) or real_txn(ops)
    return model, txns


def test_gpio_roundtrip(hal):
    dev = hal.get_device("gpio")
    with shal.approver(shal.AutoApprove()):
        dev.set_direction(0, output=True)
        dev.write_pin(0, high=True)
        assert dev.read_pin(0) is True
        dev.write_pin(0, high=False)
        assert dev.read_pin(0) is False
    assert isinstance(dev, shal.GPIOExpander)


def test_labels():
    assert inferred_side_effect(driver.Mcp23017.read_pin) == "none"
    assert inferred_side_effect(driver.Mcp23017.set_direction) == "config"
    assert inferred_side_effect(driver.Mcp23017.write_pin) == "actuator"


def test_set_direction_is_config_and_gated(hal, spy):
    # arming a pin as an output is a configuration change, so it is gated (#151)
    model, txns = spy
    asked: list[str] = []

    def ask(allow):
        return shal.CallableApprover(lambda req: asked.append(req.op) or allow)

    dev = hal.get_device("gpio")
    with shal.approver(ask(False)):
        with pytest.raises(shal.ApprovalDenied):
            dev.set_direction(0, output=True)
    assert asked == ["set_direction"]
    assert txns == [] and model.regs[0x00] == 0xFF  # nothing reached the bus
    with shal.approver(ask(True)):
        dev.set_direction(0, output=True)
        assert model.regs[0x00] == 0xFE
        asked.clear()
        dev.read_pin(0)  # a plain read never consults the approver
    assert asked == []


def test_write_pin_out_of_range_is_refused_before_io(hal, spy):
    _, txns = spy
    with shal.approver(shal.AutoApprove()):
        with pytest.raises(shal.LimitError):
            hal.get_device("gpio").write_pin(16, high=True)
    assert txns == []


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.Mcp23017, _TOPO)
    assert report.problems == []
    assert report.warnings == []
