"""MCP bridge (issues #25/#26/#27): the pure SHAL->MCP core, tested without the
`mcp` SDK. Covers tool exposure, reads-free/writes-gated, the in-band approval
two-step, and the free-writes opt-out."""
import pytest

import shal
from shal.mcp import APPROVE_TOOL, Bridge

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
