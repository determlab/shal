"""Human-in-the-loop actuation gate (issue #14).

The gate lives in the capability-wrapper, so these tests assert behavior through
the public surface (driver methods + hal.call_tool) — never the wrapper's guts.
The autouse AutoApprove fixture (conftest) is overridden per test where a real
policy decision is under test.
"""
import io

import pytest

import shal

# what the test driver's ops actually "did" — empty means nothing reached the device
RECEIVED: list[tuple[str, dict]] = []


@shal.register
class Rig(shal.Driver):
    """A device with one of each effect class, so the gate's selectivity is testable."""
    compatible = "test,approval-rig"
    kind = None

    @shal.op("Move the arm. Call to physically actuate.", side_effect="actuator")
    def move(self, dx: int) -> str:
        RECEIVED.append(("move", {"dx": dx}))
        return f"moved {dx}"

    @shal.op("Move the arm, but only within limits.", side_effect="actuator",
             params={"dx": {"maximum": 10}})
    def move_limited(self, dx: int) -> str:
        RECEIVED.append(("move_limited", {"dx": dx}))
        return f"moved {dx}"

    @shal.op("Set a register value (non-physical write).", side_effect="write")
    def set_reg(self, value: int) -> str:
        RECEIVED.append(("set_reg", {"value": value}))
        return f"reg={value}"

    @shal.op("Read the sensor.", side_effect="none")
    @shal.idempotent
    def read(self) -> int:
        RECEIVED.append(("read", {}))
        return 42


_YAML = ("shal_version: 1\n"
         "root:\n"
         "  rig: {id: rig, driver: 'test,approval-rig', address: 1}\n")


@pytest.fixture
def hal(tmp_path):
    RECEIVED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as h:
        yield h


class Spy:
    """Records every request and answers with a fixed verdict."""
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.seen: list[shal.ApprovalRequest] = []

    def approve(self, request):
        self.seen.append(request)
        return self.allow


# ---- the core guarantee: an actuator is gated, both call paths --------------------

def test_actuator_denied_sends_nothing_raw_path(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(5)
    assert RECEIVED == []  # the device never saw the command


def test_actuator_denied_via_tool_surface(hal):
    with shal.approver(shal.DenyAll()):
        out = hal.call_tool("rig__move", {"dx": 5})
    assert out["ok"] is False and out["rejected"] == "approval"
    assert "delivered" not in out  # nothing was sent -> no delivery ambiguity
    assert RECEIVED == []


def test_actuator_allowed_executes(hal):
    with shal.approver(shal.AutoApprove()):
        assert hal.get_device("rig").move(5) == "moved 5"
    assert RECEIVED == [("move", {"dx": 5})]


# ---- selectivity: only actuators are gated ---------------------------------------

def test_write_op_is_not_gated(hal):
    with shal.approver(shal.DenyAll()):  # would block an actuator, but this is a write
        assert hal.get_device("rig").set_reg(7) == "reg=7"
    assert RECEIVED == [("set_reg", {"value": 7})]


def test_read_op_is_not_gated(hal):
    with shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").read() == 42
    assert RECEIVED == [("read", {})]


# ---- ordering: limits win, the approver is never asked an impossible question -----

def test_limits_checked_before_approval(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        with pytest.raises(shal.LimitError):
            hal.get_device("rig").move_limited(99)   # over the declared maximum
    assert spy.seen == []        # approver never consulted for an impossible op
    assert RECEIVED == []        # and nothing was sent


# ---- the request the host receives -----------------------------------------------

def test_request_carries_op_path_and_params(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").move(3)
    (req,) = spy.seen
    assert req.op == "move" and req.id == "rig"
    assert req.params == {"dx": 3}
    assert req.side_effect == "actuator"
    assert req.path.endswith("/rig")


# ---- the shipped default is safe -------------------------------------------------

def test_console_approver_denies_when_not_a_tty():
    import io
    approver = shal.ConsoleApprover(stream=io.StringIO())  # not a TTY
    req = shal.ApprovalRequest(op="move", path="/rig", id="rig",
                               side_effect="actuator", params={"dx": 1}, txn="----")
    assert approver.approve(req) is False


def test_console_approver_allows_on_yes():
    class TTY(io.StringIO):
        def isatty(self):
            return True
    approver = shal.ConsoleApprover(stream=TTY(), prompt=lambda _: "y")
    req = shal.ApprovalRequest(op="move", path="/rig", id="rig",
                               side_effect="actuator", params={}, txn="----")
    assert approver.approve(req) is True


# ---- every decision is audited ---------------------------------------------------

@pytest.fixture
def audit_records():
    import logging
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level=logging.INFO)
    audit = logging.getLogger("shal.audit")  # propagate=False -> needs its own handler
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    yield records
    audit.removeHandler(handler)
    audit.setLevel(logging.NOTSET)


def test_denied_actuation_is_audited(hal, audit_records):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(1)
    denied = [r for r in audit_records if r.outcome == "denied"]
    assert denied and denied[0].op == "move" and denied[0].event == "audit"


def test_approved_actuation_is_audited(hal, audit_records):
    with shal.approver(shal.AutoApprove()):
        hal.get_device("rig").move(1)
    outcomes = [r.outcome for r in audit_records if r.op == "move"]
    assert "approved" in outcomes and "ok" in outcomes  # decision, then the I/O
