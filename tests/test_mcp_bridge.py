"""MCP bridge (issues #25/#26/#27): the pure SHAL->MCP core, tested without the
`mcp` SDK. Covers tool exposure, reads-free/writes-gated, the in-band approval
two-step, and the free-writes opt-out."""
import pytest

import shal
from shal.mcp import APPROVE_TOOL, DENY_TOOL, Bridge

# what actually reached the device — empty means nothing executed
RECEIVED: list[tuple[str, dict]] = []


@shal.register
class _McpRig(shal.Driver):
    compatible = "test,mcp-rig"
    kind = None
    llm_ready = True

    @shal.op("Read the sensor now.", side_effect="none")
    @shal.idempotent
    def read(self) -> int:
        RECEIVED.append(("read", {}))
        return 7

    @shal.op("Move the arm (physical motion).", side_effect="actuator")
    def move(self, dx: int) -> str:
        RECEIVED.append(("move", {"dx": dx}))
        return f"moved {dx}"

    @shal.op("Set a benign register value.", side_effect="write")
    def set_reg(self, value: int) -> str:
        RECEIVED.append(("set_reg", {"value": value}))
        return f"reg={value}"


_YAML = ("shal_version: 1\n"
         "root:\n"
         "  rig: {id: rig, driver: 'test,mcp-rig', address: 1}\n")


@pytest.fixture
def hal(tmp_path):
    RECEIVED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as h:
        yield h


# ---- tool exposure ---------------------------------------------------------------

def test_tool_defs_carry_mcp_hints_and_approve_tool(hal):
    defs = {d["name"]: d for d in Bridge(hal).tool_defs()}
    assert defs["rig__read"]["annotations"]["readOnlyHint"] is True
    assert defs["rig__move"]["annotations"]["destructiveHint"] is True
    assert defs["rig__set_reg"]["annotations"]["destructiveHint"] is False
    assert APPROVE_TOOL in defs  # the confirm tool is offered in gate mode


def test_free_writes_mode_offers_no_approve_tool(hal):
    defs = {d["name"]: d for d in Bridge(hal, free_writes=True).tool_defs()}
    assert APPROVE_TOOL not in defs


# ---- reads & benign writes run free ----------------------------------------------

def test_read_runs_without_approval(hal):
    out = Bridge(hal).call("rig__read", {})
    assert out == {"ok": True, "result": 7}
    assert RECEIVED == [("read", {})]


def test_benign_write_runs_without_approval(hal):
    out = Bridge(hal).call("rig__set_reg", {"value": 9})
    assert out["ok"] is True and out["result"] == "reg=9"
    assert RECEIVED == [("set_reg", {"value": 9})]


# ---- the in-band approval two-step (issue #26) -----------------------------------

def test_gated_op_returns_ticket_and_sends_nothing(hal):
    out = Bridge(hal).call("rig__move", {"dx": 5})
    assert out["ok"] is False and out["status"] == "approval_required"
    assert out["approval_id"] and out["tool"] == "rig__move"
    assert RECEIVED == []  # NOTHING reached the device


def test_confirm_executes_the_pending_call(hal):
    b = Bridge(hal)
    ticket = b.call("rig__move", {"dx": 5})
    assert RECEIVED == []
    done = b.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"]})
    assert done["ok"] is True and done["result"] == "moved 5"
    assert done["approved"] == "rig__move"
    assert RECEIVED == [("move", {"dx": 5})]


def test_confirm_is_one_shot(hal):
    b = Bridge(hal)
    ticket = b.call("rig__move", {"dx": 1})
    b.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"]})
    again = b.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"]})
    assert again["ok"] is False and "no pending approval" in again["error"]


def test_confirm_unknown_id_is_rejected(hal):
    out = Bridge(hal).call(APPROVE_TOOL, {"approval_id": "nope"})
    assert out["ok"] is False and "no pending approval" in out["error"]


def test_unknown_tool_is_rejected(hal):
    out = Bridge(hal).call("rig__nope", {})
    assert out["ok"] is False and "no tool" in out["error"]


# ---- free-writes opt-out (issue #27) ---------------------------------------------

def test_free_writes_executes_gated_op_directly(hal):
    out = Bridge(hal, free_writes=True).call("rig__move", {"dx": 3})
    assert out["ok"] is True and out["result"] == "moved 3"
    assert RECEIVED == [("move", {"dx": 3})]


# ---- approval-ticket hardening (issue #36) ---------------------------------------

def test_deny_tool_offered_in_gate_mode_and_is_safe(hal):
    defs = {d["name"]: d for d in Bridge(hal).tool_defs()}
    assert DENY_TOOL in defs
    # denying only ever PREVENTS a hardware change -> not destructive
    assert defs[DENY_TOOL]["annotations"]["destructiveHint"] is False


def test_free_writes_mode_offers_no_deny_tool(hal):
    defs = {d["name"]: d for d in Bridge(hal, free_writes=True).tool_defs()}
    assert DENY_TOOL not in defs


def test_deny_discards_the_pending_call_and_sends_nothing(hal):
    b = Bridge(hal)
    ticket = b.call("rig__move", {"dx": 4})
    out = b.call(DENY_TOOL, {"approval_id": ticket["approval_id"]})
    assert out["ok"] is False and out["denied"] == "rig__move"
    assert RECEIVED == []  # nothing reached the device


def test_denied_ticket_cannot_be_replayed_as_approve(hal):
    """A "no" is final: once denied, the same id can never be approved (#36)."""
    b = Bridge(hal)
    ticket = b.call("rig__move", {"dx": 4})
    b.call(DENY_TOOL, {"approval_id": ticket["approval_id"]})
    replay = b.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"]})
    assert replay["ok"] is False and "no pending approval" in replay["error"]
    assert RECEIVED == []


def test_deny_unknown_id_is_rejected(hal):
    out = Bridge(hal).call(DENY_TOOL, {"approval_id": "nope"})
    assert out["ok"] is False and "no pending approval" in out["error"]


def test_approval_is_bound_to_the_ticketed_args_not_the_confirm_call(hal):
    """shal_approve runs exactly the (tool, args) the human saw — extra args
    smuggled into the confirm call are ignored, never executed (#36)."""
    b = Bridge(hal)
    ticket = b.call("rig__move", {"dx": 5})
    done = b.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"],
                                 "dx": 999, "tool": "rig__set_reg"})
    assert done["ok"] is True and done["result"] == "moved 5"
    assert RECEIVED == [("move", {"dx": 5})]  # the human-seen args, not the smuggled ones


def test_tickets_do_not_survive_a_restart(hal):
    """Pending tickets live only in memory: a fresh Bridge (a process restart)
    fails closed — a ticket minted before the restart can't be redeemed (#36)."""
    b1 = Bridge(hal)
    ticket = b1.call("rig__move", {"dx": 2})
    b2 = Bridge(hal)  # restart: new process, empty pending state
    out = b2.call(APPROVE_TOOL, {"approval_id": ticket["approval_id"]})
    assert out["ok"] is False and "no pending approval" in out["error"]
    assert RECEIVED == []


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


def test_ticket_transitions_are_audited_by_approval_id(hal, audit_records):
    """requested/approved and requested/denied each land in shal.audit,
    correlated by approval_id, so a "no" is as visible as a "yes" (#36)."""
    b = Bridge(hal)
    approved = b.call("rig__move", {"dx": 1})["approval_id"]
    b.call(APPROVE_TOOL, {"approval_id": approved})
    denied = b.call("rig__move", {"dx": 2})["approval_id"]
    b.call(DENY_TOOL, {"approval_id": denied})

    rows = {(getattr(r, "outcome", None), getattr(r, "txn", None))
            for r in audit_records if getattr(r, "event", None) == "audit"}
    assert ("requested", approved) in rows
    assert ("approved", approved) in rows
    assert ("requested", denied) in rows
    assert ("denied", denied) in rows


# ---- one gate, rendered by the Bridge (issue #52) --------------------------------

def test_bridge_renders_the_one_gate_and_cant_be_silently_disabled(hal):
    """Even with an ambient 'approve everything' approver active, the Bridge in gate
    mode STILL gates — it renders the single op-layer gate in its own scope, it does
    not defer to (or get disabled by) whatever approver happens to be set."""
    with shal.approver(shal.AutoApprove()):
        out = Bridge(hal).call("rig__move", {"dx": 1})
    assert out["status"] == "approval_required"
    assert RECEIVED == []                         # nothing reached the device


def test_no_gated_write_reaches_the_device_ungated(hal):
    """A gated write is stopped pre-I/O on BOTH call paths — there is no second,
    bypassing gate."""
    with shal.approver(shal.DenyAll()):           # raw path, safe default
        raw = hal.call_tool("rig__move", {"dx": 2})
    assert raw["ok"] is False and raw["rejected"] == "approval"
    assert RECEIVED == []
    out = Bridge(hal).call("rig__move", {"dx": 3})  # Bridge path
    assert out["status"] == "approval_required"
    assert RECEIVED == []


def test_advertised_gated_set_equals_enforced(hal):
    """Advertised (`destructiveHint`) == enforced (what the single gate defers)."""
    b = Bridge(hal)
    defs = {d["name"]: d for d in b.tool_defs()}
    assert defs["rig__move"]["annotations"]["destructiveHint"] is True
    assert b.call("rig__move", {"dx": 1})["status"] == "approval_required"   # gated
    assert defs["rig__read"]["annotations"]["destructiveHint"] is False
    assert b.call("rig__read", {})["ok"] is True                             # free read
    assert defs["rig__set_reg"]["annotations"]["destructiveHint"] is False
    assert b.call("rig__set_reg", {"value": 5})["ok"] is True                # free benign write
