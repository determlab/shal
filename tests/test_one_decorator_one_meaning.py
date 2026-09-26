"""One decorator, one meaning (shal#194, ADR-001 addendum 4).

`@idempotent` says only that a lost-delivery retry is safe. The `side_effect`
label decides the gate and the audit: every op that is not "none" is audited,
and an op with no label is "actuator" (gated, audited) even when it is
`@idempotent`. Retry is unchanged: once, on delivered="no". A gated op is
approved ONCE per call — the retry does not ask again — and the call keeps ONE
outcome record, marked attempt=2 when the retry fired.
"""
import logging

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model

RECEIVED: list[dict] = []


@msg_sim_model("test,one-meaning")
class _Model:
    def handle(self, msg) -> dict:
        RECEIVED.append(dict(msg))
        return {"ok": True, "level": 7}


@shal.register
class _Psu(shal.Driver):
    compatible = "test,one-meaning"
    kind = shal.MessageTransport
    llm_ready = True

    @shal.idempotent  # an absolute setpoint: safe to send twice, and a write
    @shal.op("Set the output to an absolute level.", side_effect="write")
    def set_level(self, level: int) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "set", "level": level})["ok"]

    @shal.idempotent  # the author forgot the label: fail closed, never a read
    @shal.op("Set the output to an absolute level (unlabelled).")
    def set_level_unlabelled(self, level: int) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "set", "level": level})["ok"]

    @shal.idempotent
    @shal.op("Re-home the output (idempotent, gated).", side_effect="actuator")
    def home(self) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "home"})["ok"]

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
         "      psu: {id: psu, driver: 'test,one-meaning', address: psu1}\n")


@pytest.fixture
def rig(tmp_path):
    RECEIVED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal, hal.get_node("svc").driver, hal.get_device("psu")


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


def test_an_idempotent_write_is_audited(rig, audit):
    _, _, psu = rig
    assert psu.set_level(5) is True
    (rec,) = [r for r in audit if r.op == "set_level"]
    assert rec.event == "audit" and rec.outcome == "ok" and rec.id == "psu"
    assert rec.attempt == 1


def test_an_unlabelled_idempotent_op_is_gated_no_approval_no_io(rig, audit):
    _, _, psu = rig
    assert shal.driver.inferred_side_effect(
        type(psu).capability_ops()["set_level_unlabelled"]) == "actuator"
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            psu.set_level_unlabelled(5)
    assert RECEIVED == []                                   # nothing was sent
    assert _for(audit, "set_level_unlabelled") == [("denied", None)]


def test_an_unlabelled_idempotent_op_runs_audited_once_approved(rig, audit):
    _, _, psu = rig
    with shal.approver(shal.AutoApprove()):
        assert psu.set_level_unlabelled(5) is True
    assert _for(audit, "set_level_unlabelled") == [("approved", None), ("ok", 1)]


def test_a_none_read_is_not_audited(rig, audit):
    _, _, psu = rig
    assert psu.read_level() == 7
    assert [r for r in audit if r.op == "read_level"] == []


def test_retry_fires_once_for_an_idempotent_read(rig, audit):
    _, bus, psu = rig
    bus.fail_next = 1                        # one drop, delivered="no"
    assert psu.read_level() == 7             # reconnect once, retry once
    assert RECEIVED == [{"cmd": "get"}]      # exactly one send reached the device
    assert [r for r in audit if r.op == "read_level"] == []   # still a read


def test_retry_of_an_idempotent_write_keeps_one_outcome_record(rig, audit):
    _, bus, psu = rig
    bus.fail_next = 1
    assert psu.set_level(5) is True
    assert RECEIVED == [{"cmd": "set", "level": 5}]
    assert _for(audit, "set_level") == [("ok", 2)]


def test_retry_of_an_approved_gated_op_asks_once(rig, audit):
    _, bus, psu = rig
    asked = []

    class _Count(shal.Approver):
        def approve(self, request):
            asked.append(request.op)
            return True

    bus.fail_next = 1
    with shal.approver(_Count()):
        assert psu.home() is True
    assert asked == ["home"]                 # the retry did not ask again
    assert RECEIVED == [{"cmd": "home"}]
    assert _for(audit, "home") == [("approved", None), ("ok", 2)]


def test_two_drops_exhaust_the_retry_and_the_error_is_audited(rig, audit):
    _, bus, psu = rig
    bus.fail_next = 2
    with pytest.raises(shal.HopError):
        psu.set_level(5)
    assert RECEIVED == []
    assert _for(audit, "set_level") == [("error", 2)]
