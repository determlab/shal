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

import json
import logging

import pytest

import shal
from shal import cli
from shal.buses.sim_msg import SimMsgBus, msg_sim_model

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


# -- nested ops: one @idempotent op calling another through its own `bus` -- #

@msg_sim_model("test,retry-nested-inner")
class _InnerModel:
    def handle(self, msg: dict) -> dict:
        return {"ok": True, "level": 9}


@shal.register
class _Inner(shal.Driver):
    compatible = "test,retry-nested-inner"
    kind = shal.MessageTransport
    llm_ready = True

    @shal.idempotent
    @shal.op("Read the level.", side_effect="none")
    def read_level(self) -> int:
        return self.bus.exchange(self.addr, {"cmd": "get"})["level"]


@msg_sim_model("test,retry-nested-outer")
class _OuterModel:
    def handle(self, msg: dict) -> dict:
        return {"ok": True}


@shal.register
class _Outer(shal.Driver):
    compatible = "test,retry-nested-outer"
    kind = shal.MessageTransport
    llm_ready = True

    @shal.idempotent
    @shal.op("Check in on its own bus, then read through the inner device.",
            side_effect="none")
    def read_via_inner(self, inner) -> int:
        # a real exchange of the OUTER's own, so the outer call has its own
        # retry path to test -- delegating to `inner` alone never touches
        # `outer_bus` at all.
        self.bus.exchange(self.addr, {"cmd": "ping"})
        return inner.read_level()


_NESTED_YAML = ("shal_version: 1\n"
               "root:\n"
               "  outer_bus:\n"
               "    id: outer_bus\n"
               "    driver: shal,sim-msg\n"
               "    address: osim\n"
               "    children:\n"
               "      outer: {id: outer, driver: 'test,retry-nested-outer', address: o1}\n"
               "  inner_bus:\n"
               "    id: inner_bus\n"
               "    driver: shal,sim-msg\n"
               "    address: isim\n"
               "    children:\n"
               "      inner: {id: inner, driver: 'test,retry-nested-inner', address: i1}\n")


@pytest.fixture
def nested_rig(tmp_path):
    p = tmp_path / "nested.yaml"
    p.write_text(_NESTED_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal, hal.get_node("outer_bus").driver, hal.get_node("inner_bus").driver


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


def test_nested_outer_retry_is_not_hidden_by_an_inner_call(nested_rig):
    """CTO review round 2: the outer op's OWN drop/retry must still show on
    the result -- a nested call resetting the shared vars unconditionally
    made this read `retries: 0` while the audit line said `attempt: 2`."""
    hal, outer_bus, _ = nested_rig
    outer_bus.fail_next = 1   # the OUTER call's own first send drops
    result = hal.call_tool("outer__read_via_inner", {"inner": hal.get_device("inner")})
    assert result == {"ok": True, "result": 9, "retries": 1, "dropped": "sim-msg"}


def test_a_nested_inner_retry_is_never_credited_to_the_outer_call(nested_rig):
    """CTO review round 2: the opposite leak -- an INNER call's retry must
    not be credited to the OUTER call just because it ran inside it. The
    outer call here never retries at all."""
    hal, _, inner_bus = nested_rig
    inner_bus.fail_next = 1   # the INNER call's own first send drops
    result = hal.call_tool("outer__read_via_inner", {"inner": hal.get_device("inner")})
    assert result == {"ok": True, "result": 9, "retries": 0}
    assert "dropped" not in result


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


# --------------------------------------------------------------------------- #
# the --json output (the DoD names it explicitly): `shal call` spreads
# `Hal.call_tool`'s own result dict into its payload (cli.py), so retries/
# dropped must show up there too -- a later cli.py change that stopped
# spreading them would otherwise go unnoticed.
# --------------------------------------------------------------------------- #

def test_cli_call_json_carries_retries_1_and_dropped_on_a_drop(tmp_path, capsys, monkeypatch):
    # inject exactly one drop on the very first connect -- the real retry
    # code path (a HopError on the first send) then runs through the
    # actual `shal call ... --json` entry point. The retry's own
    # reconnect calls `activate()` again (via `ensure_ready()`); the
    # `armed` guard makes sure THAT call never re-arms the drop, or the
    # retry attempt would fail too.
    real_activate = SimMsgBus.activate
    armed = []

    def _activate_then_drop_once(self):
        real_activate(self)
        if not armed:
            armed.append(True)
            self.fail_next = 1

    monkeypatch.setattr(SimMsgBus, "activate", _activate_then_drop_once)
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")

    assert cli.main(["call", str(p), "psu", "set_level", "5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["retries"] == 1
    assert out["dropped"] == "sim-msg"


def test_cli_call_json_carries_retries_0_on_a_clean_call(tmp_path, capsys):
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")

    assert cli.main(["call", str(p), "psu", "set_level", "5", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["retries"] == 0
    assert "dropped" not in out
