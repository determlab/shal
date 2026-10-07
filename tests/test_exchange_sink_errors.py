"""issue #466: a sink that raises inside `record_exchange` must never fail
or change a bus call it is only supposed to observe. Follow-up from the
review of merged PR #457/#459 (the opt-in exchange-log hook,
`shal.log.exchange_sink`)."""
from __future__ import annotations

import logging

import pytest

import shal
from shal.errors import HopError
from shal.log import exchange_sink


def _topology():
    return {
        "shal_version": 1,
        "root": {
            "bus": {
                "id": "bus", "driver": "shal,sim-i2c", "address": "sim0",
                "children": {"t": {"id": "t", "driver": "shal,sim-sensor", "address": 0x48}},
            },
        },
    }


def _raising_sink(exchange) -> None:
    raise RuntimeError("boom: this sink is broken, on purpose")


def test_a_raising_sink_does_not_change_the_bus_call_and_logs_one_warning(caplog):
    with shal.load(_topology()) as hal:
        with caplog.at_level(logging.WARNING, logger="shal.log"), \
             exchange_sink(_raising_sink):
            reading = hal.get_device("t").read_celsius()
        # same result as with no sink at all -- the broken observer never
        # reaches the real call
        assert isinstance(reading, float)

    warnings = [r for r in caplog.records if r.name == "shal.log"]
    assert len(warnings) == 1
    assert "exchange_sink" in warnings[0].getMessage()
    # no exchange data (address/request/response) spelled out in the message
    assert "0x48" not in warnings[0].getMessage()


def test_a_real_bus_error_still_raises_its_own_exception_not_the_sinks(caplog):
    with shal.load(_topology()) as hal:
        bus = hal.get_node("bus").driver
        bus.fail_next = 2  # exhausts the one reconnect-and-retry the op allows
        with caplog.at_level(logging.WARNING, logger="shal.log"), \
             exchange_sink(_raising_sink):
            with pytest.raises(HopError):
                hal.get_device("t").read_celsius()

    # the bus's own HopError propagated -- the broken sink was never even
    # reached (record_exchange only runs after a real exchange completes),
    # so it logged nothing for this call (the driver's own unrelated
    # reconnect-and-retry WARNING is expected and not what this checks)
    assert not [r for r in caplog.records if r.name == "shal.log"]


def test_a_sink_that_does_not_raise_still_works_as_before(caplog):
    captured = []
    with shal.load(_topology()) as hal:
        with caplog.at_level(logging.WARNING, logger="shal.log"), \
             exchange_sink(captured.append):
            hal.get_device("t").read_celsius()

    assert len(captured) == 1
    assert not [r for r in caplog.records if r.name == "shal.log"]
