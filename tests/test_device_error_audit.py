"""A device refusal is an audited outcome (shal#198, ADR-001 addendum 4 / D26).

When the driver body of an audited op (side_effect != "none", a device driver)
raises a `shal.Error` — the device said no — the wrapper writes ONE outcome
record, outcome="device-error" with the error's message, and then re-raises the
SAME error. A HopError keeps its own "error" record (never both); the pre-I/O
LimitError / ApprovalDenied keep theirs; a read writes nothing.
"""
import logging
import traceback

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model

RAISED: list[BaseException] = []


class OutOfRange(shal.Error):
    """The instrument rejected the setpoint."""


@msg_sim_model("test,device-error")
class _Model:
    def handle(self, msg) -> dict:
        if msg.get("level", 0) > 9:
            return {"ok": False, "err": "-222,Data out of range"}
        return {"ok": True, "level": 7}


@shal.register
class _Psu(shal.Driver):
    compatible = "test,device-error"
    kind = shal.MessageTransport
    llm_ready = True

    def _send(self, msg):
        reply = self.bus.exchange(self.addr, msg)
        if not reply["ok"]:
            err = OutOfRange(f"device refused: {reply['err']}")
            RAISED.append(err)
            raise err
        return reply

    @shal.idempotent
    @shal.op("Set the output to an absolute level.", side_effect="write",
             params={"level": {"maximum": 100}})
    def set_level(self, level: int) -> bool:
        return self._send({"cmd": "set", "level": level})["ok"]

    @shal.op("Move the output to a level (gated).", side_effect="actuator")
    def move(self, level: int) -> bool:
        return self._send({"cmd": "move", "level": level})["ok"]

    @shal.op("Read the output level now.", side_effect="none")
    def read_level(self, level: int = 0) -> int:
        return self._send({"cmd": "get", "level": level})["level"]

    @shal.op("A driver bug, not a device answer.", side_effect="write")
    def buggy(self) -> None:
        raise TypeError("driver bug")


_YAML = ("shal_version: 1\n"
         "root:\n"
         "  svc:\n"
         "    id: svc\n"
         "    driver: shal,sim-msg\n"
         "    address: sim0\n"
         "    children:\n"
         "      psu: {id: psu, driver: 'test,device-error', address: psu1}\n")


@pytest.fixture
def rig(tmp_path):
    RAISED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal.get_node("svc").driver, hal.get_device("psu")


@pytest.fixture
def audit():
    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collect(level=logging.INFO)
    log = logging.getLogger("shal.audit")  # propagate=False: needs its own handler
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    yield records
    log.removeHandler(handler)
    log.setLevel(logging.NOTSET)


def _for(records, op):
    return [(r.outcome, getattr(r, "attempt", None)) for r in records if r.op == op]


def test_a_device_refusal_on_a_write_is_audited_and_the_same_error_raises(rig, audit):
    _, psu = rig
    with pytest.raises(OutOfRange) as info:
        psu.set_level(50)
    assert info.value is RAISED[0]                  # the SAME object, not a copy
    # the traceback still reaches the driver's raise site (re-raised untouched)
    frames = [f.name for f in traceback.extract_tb(info.value.__traceback__)]
    assert frames[-1] == "_send"
    assert _for(audit, "set_level") == [("device-error", 1)]
    (rec,) = [r for r in audit if r.op == "set_level"]
    assert rec.event == "audit" and rec.id == "psu" and rec.path
    assert "device refused: -222,Data out of range" in rec.getMessage()
    assert not hasattr(rec, "delivered")            # it WAS delivered: the device answered
    assert not hasattr(rec, "hop")                  # no retry fired


def test_a_device_refusal_on_a_gated_op_keeps_the_attempt_and_the_outcome(rig, audit):
    _, psu = rig
    with shal.approver(shal.AutoApprove()):
        with pytest.raises(OutOfRange) as info:
            psu.move(50)
    assert info.value is RAISED[0]
    # the attempt (approval) AND exactly one outcome record
    assert _for(audit, "move") == [("approved", None), ("device-error", 1)]
    assert len({r.txn for r in audit if r.op == "move"}) == 1   # one call, one txn


def test_a_refusal_after_the_idempotent_retry_carries_attempt_2_and_the_hop(rig, audit):
    bus, psu = rig
    bus.fail_next = 1                     # first send dropped (delivered="no")
    with pytest.raises(OutOfRange):
        psu.set_level(50)                 # the retry reaches the device, which says no
    assert len(RAISED) == 1               # the refusal itself is never retried
    assert _for(audit, "set_level") == [("device-error", 2)]
    (rec,) = [r for r in audit if r.op == "set_level"]
    assert rec.hop == "sim-msg"


def test_a_device_refusal_is_not_retried(rig, audit):
    _, psu = rig
    with pytest.raises(OutOfRange):
        psu.set_level(50)
    assert len(RAISED) == 1               # the driver body ran once
    assert _for(audit, "set_level") == [("device-error", 1)]


def test_a_none_read_that_raises_writes_no_audit_record(rig, audit):
    _, psu = rig
    with pytest.raises(OutOfRange):
        psu.read_level(50)
    assert audit == []


def test_a_hop_error_keeps_its_one_error_record_and_no_device_error(rig, audit):
    bus, psu = rig
    bus.fail_next = 2                     # retry exhausted: a HopError, a shal.Error too
    with pytest.raises(shal.HopError):
        psu.set_level(5)
    assert _for(audit, "set_level") == [("error", 2)]
    (rec,) = [r for r in audit if r.op == "set_level"]
    assert rec.delivered == "no" and rec.hop == "sim-msg"


def test_a_delivery_unknown_hop_error_keeps_its_one_error_record(rig, audit):
    bus, psu = rig
    bus.fail_delivered_unknown = True
    with pytest.raises(shal.HopError):
        psu.set_level(5)
    assert _for(audit, "set_level") == [("error", 1)]


def test_a_limit_rejection_is_not_also_a_device_error(rig, audit):
    _, psu = rig
    with pytest.raises(shal.LimitError):
        psu.set_level(500)
    assert _for(audit, "set_level") == [("rejected", None)]


def test_an_approval_denial_is_not_also_a_device_error(rig, audit):
    _, psu = rig
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            psu.move(50)
    assert _for(audit, "move") == [("denied", None)]


def test_a_non_shal_exception_writes_no_outcome_record(rig, audit):
    # scope of #198 is shal.Error (the device said no); a driver bug is not
    # a device answer and is left to a follow-up
    _, psu = rig
    with pytest.raises(TypeError):
        psu.buggy()
    assert _for(audit, "buggy") == []
