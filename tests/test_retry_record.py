"""issue #348: the idempotent reconnect-and-retry (driver.py, inside
`call`) was visible only as a WARNING log line. `retries` (0 when the op
was not retried, 1 after the one retry) and, when a retry happened,
`dropped` (the hop that dropped) are now additive fields on the call
result (`Hal.call_tool`'s dict -- the same one `--json` serializes) and on
the audit line, alongside the existing `attempt`/`hop`. No change to when
or how the retry fires: it is still exactly `delivered == "no"`, idempotent,
unrouted.
"""
from __future__ import annotations

import logging

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model

RECEIVED: list[dict] = []


@msg_sim_model("test,retry-record")
class _Model:
    def handle(self, msg) -> dict:
        RECEIVED.append(dict(msg))
        return {"ok": True, "level": 7}


@shal.register
class _Psu(shal.Driver):
    compatible = "test,retry-record"
    kind = shal.MessageTransport
    llm_ready = True

    @shal.idempotent
    @shal.op("Set the output to an absolute level.", side_effect="write")
    def set_level(self, level: int) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "set", "level": level})["ok"]

    # no @idempotent: a write this framework must never auto-retry
    @shal.op("Nudge the output by a relative amount.", side_effect="write")
    def nudge(self, delta: int) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "nudge", "delta": delta})["ok"]

    @shal.idempotent
    @shal.op("Read the output level now.", side_effect="none")
    def read_level(self) -> int:
        return self.bus.exchange(self.addr, {"cmd": "get"})["level"]


_YAML = ("shal_version: 1\n"
         "root:\n"
         "  svc:\n"
         "    id: svc\n"
         "    driver: shal,sim-msg\n"
         "    address: sim0\n"
         "    children:\n"
         "      psu: {id: psu, driver: 'test,retry-record', address: psu1}\n")


@pytest.fixture
def rig(tmp_path):
    RECEIVED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal, hal.get_node("svc").driver


@pytest.fixture
def audit():
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect(level=logging.INFO)
    log = logging.getLogger("shal.audit")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield records
    log.removeHandler(handler)
    log.setLevel(logging.NOTSET)


def _audit_for(records, op):
    return [r for r in records if r.op == op]


# --------------------------------------------------------------------------- #
# the call result (Hal.call_tool's dict -- what `--json` serializes)
# --------------------------------------------------------------------------- #

def test_a_dropped_first_attempt_gives_retries_1_and_the_dropped_hop(rig):
    hal, bus = rig
    bus.fail_next = 1   # first send dropped (delivered="no"); the retry reaches it
    result = hal.call_tool("psu__set_level", {"level": 5})
    assert result == {"ok": True, "result": True, "retries": 1, "dropped": "sim-msg"}


def test_a_clean_call_gives_retries_0_and_no_dropped_key(rig):
    hal, _ = rig
    result = hal.call_tool("psu__set_level", {"level": 5})
    assert result == {"ok": True, "result": True, "retries": 0}
    assert "dropped" not in result


def test_retries_resets_between_calls_on_the_same_bus(rig):
    """A retried call must not leak `retries: 1` into the NEXT, clean call."""
    hal, bus = rig
    bus.fail_next = 1
    first = hal.call_tool("psu__set_level", {"level": 1})
    assert first["retries"] == 1
    second = hal.call_tool("psu__set_level", {"level": 2})
    assert second["retries"] == 0
    assert "dropped" not in second


# --------------------------------------------------------------------------- #
# cases where the retry never fires: retries: 0, nothing retried
# --------------------------------------------------------------------------- #

def test_a_non_idempotent_op_is_never_retried(rig):
    hal, bus = rig
    bus.fail_next = 1
    result = hal.call_tool("psu__nudge", {"delta": 1})
    assert result["ok"] is False
    assert result["delivered"] == "no"
    assert result["retries"] == 0
    assert "dropped" not in result
    assert len(RECEIVED) == 0   # never reached the device at all -- no retry sent it


def test_a_delivered_unknown_failure_is_never_retried(rig):
    """The retry fires only on `delivered == "no"` -- a connection lost
    AFTER send (`delivered == "unknown"`) is never auto-retried, since the
    device may already have acted on it once."""
    hal, bus = rig
    bus.fail_delivered_unknown = True
    result = hal.call_tool("psu__set_level", {"level": 5})
    assert result["ok"] is False
    assert result["delivered"] == "unknown"
    assert result["retries"] == 0
    assert "dropped" not in result


# --------------------------------------------------------------------------- #
# the audit line: additive fields, the existing attempt/hop are unchanged
# --------------------------------------------------------------------------- #

def test_audit_line_carries_retries_and_dropped_alongside_attempt_and_hop(rig, audit):
    hal, bus = rig
    bus.fail_next = 1
    hal.call_tool("psu__set_level", {"level": 5})
    (rec,) = _audit_for(audit, "set_level")
    assert rec.attempt == 2 and rec.retries == 1
    assert rec.hop == "sim-msg" and rec.dropped == "sim-msg"


def test_audit_line_on_a_clean_call_has_retries_0_and_no_dropped(rig, audit):
    hal, _ = rig
    hal.call_tool("psu__set_level", {"level": 5})
    (rec,) = _audit_for(audit, "set_level")
    assert rec.attempt == 1 and rec.retries == 0
    assert not hasattr(rec, "hop")
    assert not hasattr(rec, "dropped")
