"""issue #466: a sink that raises inside `record_exchange` must never fail
or change a bus call it is only supposed to observe -- in particular a
DELIVERED write: `scpi_raw`/`sim_msg`'s envelope path both call
`record_exchange` only AFTER the device has already acted, so a raising
sink must never turn something that really happened into a reported
failure. Follow-up from the review of merged PR #457/#459 (the opt-in
exchange-log hook, `shal.log.exchange_sink`)."""
from __future__ import annotations

import logging

import pytest

import shal
from shal import registry
from shal.buses.sim import sim_model
from shal.buses.sim_msg import msg_sim_model
from shal.buses.sim_scpi import scpi_sim_model
from shal.driver import Driver
from shal.errors import HopError
from shal.log import Exchange, exchange_sink, record_exchange
from shal.transport import ByteTransport, MessageTransport, Read, Write


def _register_dummy_driver(compatible: str, kind: type) -> None:
    cls = type(f"_Dummy_{compatible.replace(',', '_').replace('-', '_')}",
              (Driver,), {"compatible": compatible, "kind": kind})
    registry.register(cls)


def _raising_sink(exchange) -> None:
    raise RuntimeError("boom: this sink is broken, on purpose")


def _keyboard_interrupt_sink(exchange) -> None:
    raise KeyboardInterrupt


def _warnings(caplog):
    return [r for r in caplog.records if r.name == "shal.log"]


# -- deterministic, COUNTING models: a changing op's delivery can be proven
# exactly-once this way, not just "the call didn't crash" (CTO review on
# #466 round 2, must-fix 1) --------------------------------------------------

@sim_model("test,sink-guard-i2c")
class _CountingI2cModel:
    def __init__(self) -> None:
        self.writes = 0

    def txn(self, ops) -> bytes:
        self.writes += sum(1 for op in ops if isinstance(op, Write))
        return b"\x2a"


@scpi_sim_model("test,sink-guard-scpi")
class _CountingScpiModel:
    def __init__(self) -> None:
        self.calls = 0

    def scpi(self, cmd: str) -> str:
        self.calls += 1
        return "42"


@msg_sim_model("test,sink-guard-msg")
class _CountingMsgModel:
    def __init__(self) -> None:
        self.calls = 0

    def handle(self, msg) -> dict:
        self.calls += 1
        return {"ok": True, "n": self.calls}


_register_dummy_driver("test,sink-guard-i2c", ByteTransport)
_register_dummy_driver("test,sink-guard-scpi", MessageTransport)
_register_dummy_driver("test,sink-guard-msg", MessageTransport)


def _i2c_topology():
    return {"shal_version": 1, "root": {"bus": {
        "id": "bus", "driver": "shal,sim-i2c", "address": "sim0",
        "children": {"d": {"id": "d", "driver": "test,sink-guard-i2c", "address": 0x10}}}}}


def _scpi_topology():
    return {"shal_version": 1, "root": {"bus": {
        "id": "bus", "driver": "shal,sim-scpi", "address": "sim0",
        "children": {"d": {"id": "d", "driver": "test,sink-guard-scpi", "address": "d0"}}}}}


def _msg_topology():
    return {"shal_version": 1, "root": {"bus": {
        "id": "bus", "driver": "shal,sim-msg", "address": "sim0",
        "children": {"d": {"id": "d", "driver": "test,sink-guard-msg", "address": "d0"}}}}}


def _i2c_write(hal):
    return hal.get_node("bus").driver.txn(0x10, [Write(bytes([1, 2, 3])), Read(1)])


def _scpi_write(hal):
    return hal.get_node("bus").driver.exchange("d0", {"scpi": "SET:X 1", "query": False})


def _msg_write(hal):
    return hal.get_node("bus").driver.exchange("d0", {"cmd": "set", "value": 1})


def _msg_envelope_write(hal):
    return hal.get_node("bus").driver.exchange(
        "d0", {"method": "POST", "path": "x", "json": {"value": 1}})


def _i2c_write_count(hal):
    return hal.get_node("bus").driver.model_for(0x10).writes


def _scpi_call_count(hal):
    return hal.get_node("bus").driver.model_for("d0").calls


def _msg_call_count(hal):
    return hal.get_node("bus").driver.model_for("d0").calls


_FAMILIES = [
    ("sim_i2c", _i2c_topology, _i2c_write, _i2c_write_count),
    ("sim_scpi", _scpi_topology, _scpi_write, _scpi_call_count),
    ("sim_msg plain", _msg_topology, _msg_write, _msg_call_count),
    ("sim_msg envelope", _msg_topology, _msg_envelope_write, _msg_call_count),
]
_FAMILY_IDS = [f[0] for f in _FAMILIES]


@pytest.mark.parametrize("name,topology,do_write,call_count", _FAMILIES, ids=_FAMILY_IDS)
def test_a_raising_sink_never_changes_a_delivered_write(name, topology, do_write, call_count):
    """must-fix 1: the result with a raising sink must equal the result
    with no sink at all, and the sim model must see the write EXACTLY
    once -- never re-sent because the sink (not the call) failed."""
    with shal.load(topology()) as hal:
        result_no_sink = do_write(hal)
        count_no_sink = call_count(hal)

    with shal.load(topology()) as hal, exchange_sink(_raising_sink):
        result_with_sink = do_write(hal)
        count_with_sink = call_count(hal)

    assert result_with_sink == result_no_sink
    assert count_with_sink == count_no_sink == 1


@pytest.mark.parametrize("name,topology,do_write,call_count", _FAMILIES, ids=_FAMILY_IDS)
def test_a_raising_sink_logs_exactly_one_warning_naming_itself(name, topology, do_write,
                                                                call_count, caplog):
    with shal.load(topology()) as hal:
        with caplog.at_level(logging.WARNING, logger="shal.log"), exchange_sink(_raising_sink):
            do_write(hal)

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    # must-fix 3: the sink itself is named (never the exchange it saw)
    assert "_raising_sink" in warnings[0].getMessage()
    assert "0x10" not in warnings[0].getMessage()


# -- a direct unit test of record_exchange itself (must-fix 2: the earlier
# bus-error test enforced nothing -- every record_exchange call in every
# bus sits after the last raise, so a FAILED call never even reaches the
# sink; that is a true, useful fact, kept below under its own name, but it
# is not a test of the guard this issue adds) --------------------------------

def test_record_exchange_itself_swallows_a_raising_sink_and_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="shal.log"), exchange_sink(_raising_sink):
        result = record_exchange("test", "/x", "addr", "REQ-MARKER", "RESP-MARKER")
    assert result is None
    warnings = _warnings(caplog)
    assert len(warnings) == 1
    # must-fix 1 (263a116 review): the sink's own name is the ONE %s arg --
    # never the exchange it saw, logged lazily so `getMessage()` is the
    # real proof (an f-string in the call site would pass this even if the
    # log statement later embedded the exchange too).
    assert warnings[0].args == ("_raising_sink",)
    message = warnings[0].getMessage()
    assert "REQ-MARKER" not in message
    assert "RESP-MARKER" not in message


def test_record_exchange_itself_lets_keyboardinterrupt_propagate(caplog):
    with caplog.at_level(logging.WARNING, logger="shal.log"), \
         exchange_sink(_keyboard_interrupt_sink):
        with pytest.raises(KeyboardInterrupt):
            record_exchange("test", "/x", "addr", "req", "resp")
    assert not _warnings(caplog)


def test_record_exchange_is_a_noop_before_the_try_when_no_sink_is_active(monkeypatch):
    # must-fix 2 (263a116 review): "no-op" means no redaction work runs
    # either, not just "returns None" -- moving the Exchange build (and
    # its redaction) above the `if sink is None: return` still returned
    # None, so the old assertion never caught that. Counting calls to the
    # two redaction entry points pins "no sink -> neither ever runs".
    import shal.log as log_mod

    calls: list[str] = []
    monkeypatch.setattr(log_mod, "redact_url", lambda v: calls.append("redact_url"))
    monkeypatch.setattr(log_mod, "_clean_payload", lambda v: calls.append("_clean_payload"))

    assert record_exchange("test", "/x", "addr", "req", "resp") is None
    assert calls == []


def test_redaction_runs_outside_the_guard_so_a_redaction_bug_still_raises(caplog, monkeypatch):
    """A `redact_url`/`_clean_payload` bug must still fail loudly -- the
    guard protects only `sink(...)`, never the Exchange building above it
    (CTO review on #466 round 2: confirmed correct, pinned here)."""
    import shal.log as log_mod

    def _broken_redact(value):
        raise RuntimeError("redaction itself is broken")

    monkeypatch.setattr(log_mod, "redact_url", _broken_redact)
    with caplog.at_level(logging.WARNING, logger="shal.log"), exchange_sink(lambda e: None):
        with pytest.raises(RuntimeError, match="redaction itself is broken"):
            record_exchange("test", "/x", "addr", "req", "resp")
    assert not _warnings(caplog)  # never swallowed as if it were the sink's own failure


def test_sink_not_reached_on_a_failed_call(caplog):
    """A genuinely failed bus call (exhausted retry) never even reaches
    the sink -- record_exchange is called only after a real exchange
    completes, in every bus. This is a true, useful fact about today's
    code; it enforces nothing about #466's own guard (see the
    record_exchange-level tests above for that)."""
    topology = {"shal_version": 1, "root": {"bus": {
        "id": "bus", "driver": "shal,sim-i2c", "address": "sim0",
        "children": {"t": {"id": "t", "driver": "shal,sim-sensor", "address": 0x48}}}}}
    with shal.load(topology) as hal:
        bus = hal.get_node("bus").driver
        bus.fail_next = 2  # exhausts the one reconnect-and-retry the op allows
        with caplog.at_level(logging.WARNING, logger="shal.log"), exchange_sink(_raising_sink):
            with pytest.raises(HopError):
                hal.get_device("t").read_celsius()

    assert not _warnings(caplog)


def test_a_sink_that_does_not_raise_still_works_as_before():
    captured: list[Exchange] = []
    with shal.load(_scpi_topology()) as hal, exchange_sink(captured.append):
        _scpi_write(hal)
    assert len(captured) == 1
