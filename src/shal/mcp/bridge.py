"""SHAL → MCP bridge (issues #25/#26/#27): the pure, testable core.

Turns a loaded `Hal` into MCP tool definitions and dispatches calls. Two safety
properties live here, independent of any host:

  * **Reads run free, writes are gated** (#27) — the gated set is read from the
    tool catalog's `destructiveHint`, which `inferred_side_effect` keeps in lock-
    step with what the framework actually enforces (advertised == enforced).
  * **Host-agnostic in-band approval** (#26) — a gated op is NEVER executed on
    first call. The bridge returns an `approval_required` ticket; the host shows
    it to a human; the human authorizes the separate, destructive-flagged
    ``shal_approve`` tool, which executes the original call. Approval is thus an
    explicit, named, auditable step — never implicit in the action itself.

A free-writes opt-out (#27) is available and is recorded in the audit log the
moment it is enabled — it is a deliberate choice, never the default.

No `mcp` import here: this module is unit-tested without the SDK installed.
"""
from __future__ import annotations

import logging

import shal

from .. import log as _log

_audit = logging.getLogger("shal.audit")

APPROVE_TOOL = "shal_approve"


class Bridge:
    """Adapts one loaded `Hal` to the MCP tool surface."""

    def __init__(self, hal, *, free_writes: bool = False) -> None:
        self.hal = hal
        self.free_writes = free_writes
        # destructiveHint == gated (actuator/config), kept honest by issue #19
        self._gated = {t["name"]: bool(t["annotations"].get("destructiveHint"))
                       for t in hal.tool_catalog()}
        self._pending: dict[str, tuple[str, dict]] = {}
        if free_writes:
            # a deliberate downgrade is on the record the instant it takes effect
            _audit.info("free-writes mode enabled: gated ops run WITHOUT approval",
                        extra={"event": "audit", "op": "*", "outcome": "free_writes",
                               "txn": _log.current_txn.get()})

    # -- tool catalog ---------------------------------------------------------
    def tool_defs(self) -> list[dict]:
        """MCP tool definitions: every device op, plus (in gate mode) the
        ``shal_approve`` confirm tool. Each carries MCP annotation hints so a
        host can auto-allow reads and prompt on writes."""
        ann = {t["name"]: t["annotations"] for t in self.hal.tool_catalog()}
        defs = []
        for d in self.hal.tool_schemas():
            defs.append({**d, "annotations": ann.get(d["name"], {})})
        if not self.free_writes:
            defs.append({
                "name": APPROVE_TOOL,
                "description": (
                    "Confirm and execute a hardware action that is waiting for "
                    "human approval. Call this ONLY after a human has explicitly "
                    "approved the pending action identified by approval_id (returned "
                    "by the earlier 'approval_required' result). Do not self-approve."),
                "input_schema": {
                    "type": "object",
                    "properties": {"approval_id": {
                        "type": "string",
                        "description": "The approval_id from the approval_required result."}},
                    "required": ["approval_id"],
                },
                "annotations": {"destructiveHint": True, "readOnlyHint": False},
            })
        return defs

    # -- dispatch -------------------------------------------------------------
    def call(self, name: str, arguments: dict | None = None) -> dict:
        """Dispatch one tool call. Reads/benign writes run; gated ops return an
        ``approval_required`` ticket (pre-I/O, nothing sent) unless free-writes
        is on; ``shal_approve`` executes a previously-ticketed call."""
        arguments = dict(arguments or {})
        if name == APPROVE_TOOL:
            return self._confirm(str(arguments.get("approval_id", "")))
        if name not in self._gated:
            return {"ok": False, "error": f"no tool '{name}' (see tools/list)"}
        if self.free_writes:
            with shal.approver(shal.AutoApprove()):
                return self.hal.call_tool(name, arguments)
        if self._gated[name]:
            # gate: do NOT execute — hand back a ticket for a human to authorize
            approval_id = _log.new_txn()
            self._pending[approval_id] = (name, arguments)
            return {
                "ok": False,
                "status": "approval_required",
                "approval_id": approval_id,
                "tool": name,
                "arguments": arguments,
                "message": (
                    f"'{name}' changes hardware and needs human approval — nothing "
                    f"has been sent. After a human approves, call '{APPROVE_TOOL}' "
                    f"with approval_id='{approval_id}'."),
            }
        return self.hal.call_tool(name, arguments)  # read or benign write

    def _confirm(self, approval_id: str) -> dict:
        pending = self._pending.pop(approval_id, None)
        if pending is None:
            return {"ok": False,
                    "error": f"no pending approval '{approval_id}' "
                             f"(it may have already run, or never existed)"}
        name, arguments = pending
        with shal.approver(shal.AutoApprove()):  # one-shot: the human said yes
            result = self.hal.call_tool(name, arguments)
        return {**result, "approved": name}
