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

    @shal.op("Wipe stored maps (destructive config).", side_effect="config")
    def factory_reset(self) -> str:
        RECEIVED.append(("factory_reset", {}))
        return "wiped"

    @shal.op("Re-home the arm (idempotent physical motion).", side_effect="actuator")
    @shal.idempotent
    def home(self) -> str:
        RECEIVED.append(("home", {}))
        return "homed"

    @shal.op("Move with an optional speed (defaulted param).", side_effect="actuator")
    def move_at(self, dx: int, speed: int = 5) -> str:
        RECEIVED.append(("move_at", {"dx": dx, "speed": speed}))
        return f"moved {dx}@{speed}"

    @shal.op("Adjust the thing.")  # author FORGOT side_effect (issue #19 repro)
    def unclassified(self, x: int) -> str:
        RECEIVED.append(("unclassified", {"x": x}))
        return f"did {x}"

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


# ---- fail-closed: an un-annotated state-changer is gated (issue #19) --------------

def test_unannotated_state_changer_is_gated(hal):
    # author wrote @op but forgot side_effect; the safe default must GATE it
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").unclassified(1)
    assert RECEIVED == []  # nothing reached the device


def test_unannotated_state_changer_infers_actuator(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").unclassified(2)
    (req,) = spy.seen
    assert req.side_effect == "actuator"  # fail-closed inference, not ungated "write"
    assert RECEIVED == [("unclassified", {"x": 2})]


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
    # the decision is recorded BEFORE the I/O outcome (order: approval -> I/O)
    assert outcomes == ["approved", "ok"]


# ---- config (destructive) ops are gated too (issue #14 ADR: actuator AND config) -

def test_config_op_is_gated_and_denied(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").factory_reset()
    assert RECEIVED == []  # destructive config never reached the device


def test_config_op_allowed_executes(hal):
    with shal.approver(shal.AutoApprove()):
        assert hal.get_device("rig").factory_reset() == "wiped"
    assert RECEIVED == [("factory_reset", {})]


# ---- an @idempotent actuator is still gated AND its decision is audited (fix #1) --

def test_idempotent_actuator_is_gated(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").home()
    assert RECEIVED == []


def test_idempotent_actuator_decision_is_audited(hal, audit_records):
    # `home` is @idempotent (audited=False) but gated — the decision must still log
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").home()
    denied = [r for r in audit_records if r.op == "home" and r.outcome == "denied"]
    assert denied, "an idempotent actuator's approval decision must be audited"


# ---- the request carries defaulted params (apply_defaults path) -------------------

def test_request_includes_defaulted_params(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").move_at(3)  # speed defaults to 5
    (req,) = spy.seen
    assert req.params == {"dx": 3, "speed": 5}


# ---- the shipped DEFAULT policy denies end-to-end through the wrapper (fix #4) ----

def test_default_console_policy_denies_headless_through_wrapper(hal):
    # the real default approver (ConsoleApprover), headless (non-TTY), must deny an
    # actuator call THROUGH the wrapper — guards against the gate being skipped or
    # the default flipped. (The autouse AutoApprove fixture is overridden here.)
    with shal.approver(shal.ConsoleApprover(stream=io.StringIO())):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(1)
    assert RECEIVED == []


def test_default_approver_is_console_when_context_unset():
    # with no policy installed in this context, the fallback is ConsoleApprover
    token = shal.approval._current.set(None)
    try:
        assert isinstance(shal.get_approver(), shal.ConsoleApprover)
    finally:
        shal.approval._current.reset(token)
