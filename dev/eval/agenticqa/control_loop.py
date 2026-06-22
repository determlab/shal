"""agenticQA control loop — device-agnostic release-acceptance over the SHAL gate (#78).

Drives ONE state-changing write through the REAL MCP Bridge gate and proves the device
actually moved — for ANY device, parameterized entirely by a per-device *manuscript*
(``dev/eval/manuscripts/<device>.yaml``). Nothing in this module names a device.

This is the hermetic **Tier 1** form: in-process, no subprocess, no LLM, no network. It
exercises the SAME gate a real agent hits over MCP — the ``Bridge``'s
``approval_required`` -> ``shal_approve`` / ``shal_deny`` ticket lifecycle — with the
approval **decision** supplied as a pluggable ``approver_fn`` (the ``CallableApprover``
spirit). **Tier 2** swaps ``approver_fn`` for a separate ``agenticQA-approver`` agent on
a shared MCP server; this loop is unchanged. The operator never approves its own write.

Anti-cheat — a green that bypassed the gate must read as RED (#78):
  * the manuscript declares ``expected.gated``; the loop asserts the LIVE tool catalog
    agrees (``destructiveHint``). A gated op that silently downgrades to a benign write
    fails here.
  * for a gated op, the bare first call MUST return ``approval_required`` AND leave the
    device unmoved (nothing sent pre-approval) — proven by a read-back, not by trust.
  * pass/fail is ALWAYS a device read-back equality, never an opinion.
  * the ``shal.audit`` stream must show the ``requested`` -> ``approved`` | ``denied``
    transition for the ticket.

Usage::

    from control_loop import load_manuscript, run_control
    v = run_control(load_manuscript("dev/eval/manuscripts/deebot.yaml"))
    assert v["passed"], v["reason"]
"""
from __future__ import annotations

import importlib
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

import shal
from shal.mcp.bridge import APPROVE_TOOL, DENY_TOOL, Bridge

# A decision function: given the pending ticket (the bridge's approval_required dict),
# return "approve" or "deny". The default policy approves iff the op is exactly the one
# actuation this manuscript declared — the CallableApprover spirit, as a pure function.
ApproverFn = Callable[[dict], str]

_TICKET_OUTCOMES = ("requested", "approved", "denied")


def repo_root() -> Path:
    """Repo root, inferred from this file (dev/eval/agenticqa/control_loop.py)."""
    return Path(__file__).resolve().parents[3]


def load_manuscript(path: str | Path) -> dict:
    """Load + lightly validate a manuscript. Raises ValueError on a missing key so a
    typo fails loudly rather than skipping a check."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = ("device", "topology", "node", "drivers", "liveness_reads",
                "actuation", "expected", "teardown")
    missing = [k for k in required if k not in data]
    if missing:
        raise ValueError(f"manuscript {path} missing keys: {missing}")
    if data.get("schema_version") != 1:
        raise ValueError(f"manuscript {path} schema_version must be 1")
    if "op" not in data["actuation"]:
        raise ValueError(f"manuscript {path} actuation.op is required")
    for k in ("read", "becomes", "gated"):
        if k not in data["expected"]:
            raise ValueError(f"manuscript {path} expected.{k} is required")
    return data


def default_approver(actuation_op: str) -> ApproverFn:
    """Policy: approve only the single expected actuation; deny anything else."""
    def _fn(ticket: dict) -> str:
        # the ticket carries the resolved tool name "<node>__<op>"
        return "approve" if str(ticket.get("tool", "")).endswith(f"__{actuation_op}") else "deny"
    return _fn


class _AuditCapture(logging.Handler):
    """Collect shal.audit ticket transitions (requested/approved/denied) for one run."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[tuple[str, str]] = []  # (outcome, txn)

    def emit(self, record: logging.LogRecord) -> None:
        outcome = getattr(record, "outcome", None)
        if outcome in _TICKET_OUTCOMES:
            self.records.append((outcome, getattr(record, "txn", "")))


def _import_drivers(driver_paths: list[str], root: Path) -> None:
    """Import each driver module by NAME (so Python caches it — re-running a manuscript
    in the same process does not re-execute @register and double-register a compatible)."""
    for rel in driver_paths:
        p = (root / rel).resolve()
        parent = str(p.parent)
        if parent not in sys.path:
            sys.path.insert(0, parent)
        importlib.import_module(p.stem)


def _resolve_tools(bridge: Bridge, node: str, ops: list[str]) -> dict[str, dict]:
    """Discover the live tool catalog and resolve each bare op to its real tool def.
    Never hardcode a tool name — match the discovered "<node>__<op>" (#78 persona rule)."""
    defs = {d["name"]: d for d in bridge.tool_defs()}
    out: dict[str, dict] = {}
    for op in ops:
        name = f"{node}__{op}"
        if name not in defs:  # fall back to suffix match if the node id differs
            cands = [n for n in defs if n.endswith(f"__{op}")]
            if len(cands) != 1:
                raise ValueError(f"op '{op}' not uniquely discoverable (candidates={cands})")
            name = cands[0]
        out[op] = defs[name]
    return out


class _ReadFailed(RuntimeError):
    """A device read came back ok=False. Surfaced as a FAIL verdict, never a crash — a flaky
    ground-truth read on a real robot (Tier 2 warm-up) must read red, not look like a bug."""


def _read(bridge: Bridge, tool_name: str) -> Any:
    res = bridge.call(tool_name)
    if not res.get("ok"):
        raise _ReadFailed(f"read '{tool_name}' failed: {res}")
    return res.get("result")


def _teardown_steps(manuscript: dict) -> list[tuple[str, dict]]:
    """Normalize teardown entries: a bare op string, or a ``{op, args}`` mapping (mirrors
    ``actuation``) so a device whose safe state needs a parameter stays manuscript-only."""
    out: list[tuple[str, dict]] = []
    for entry in manuscript.get("teardown") or []:
        if isinstance(entry, dict):
            out.append((entry["op"], entry.get("args") or {}))
        else:
            out.append((entry, {}))
    return out


def run_control(manuscript: dict, *, decision: str = "approve",
                approver_fn: ApproverFn | None = None,
                free_writes: bool = False) -> dict:
    """Run the loop once for ``decision`` in {"approve", "deny"}. Returns a verdict dict;
    ``verdict["passed"]`` is True only when every gate + ground-truth check held. Logical
    failures (gate bypassed, device moved pre-approval, wrong end state) come back as
    ``passed=False`` with a ``reason`` — they never raise, so a regression shows as a red
    assertion, not a crash.

    ``free_writes`` exists for the adversarial self-test only: it turns the real gate OFF
    (the bridge runs gated ops without a ticket). A gated manuscript run with it MUST go
    red — that is the executable proof of "a green that bypassed the gate is a red" (#78)."""
    root = repo_root()
    device = manuscript["device"]
    node = manuscript["node"]
    act_op = manuscript["actuation"]["op"]
    act_args = manuscript["actuation"].get("args") or {}
    exp = manuscript["expected"]
    approver_fn = approver_fn or default_approver(act_op)

    verdict: dict[str, Any] = {
        "device": device, "decision": decision, "verdict": "FAIL", "passed": False,
        "gate_exercised": False, "gated_declared": bool(exp["gated"]), "gated_actual": None,
        "approval_id": None, "state_before": None, "state_after": None,
        "expected_becomes": exp["becomes"], "audit_transitions": [], "reason": "",
    }

    def fail(reason: str) -> dict:
        verdict["reason"] = reason
        return verdict

    _import_drivers(manuscript["drivers"], root)
    hal = shal.load(root / manuscript["topology"])
    try:
        bridge = Bridge(hal, free_writes=free_writes)  # gate ON unless the self-test disables it
        teardown = _teardown_steps(manuscript)
        all_ops = (manuscript["liveness_reads"] + [act_op, exp["read"]]
                   + [op for op, _a in teardown])
        tools = _resolve_tools(bridge, node, sorted(set(all_ops)))
        act_tool = tools[act_op]["name"]
        read_tool = tools[exp["read"]]["name"]

        # 1) liveness — every declared read returns live data.
        for r in manuscript["liveness_reads"]:
            if _read(bridge, tools[r]["name"]) in (None, ""):
                return fail(f"liveness read '{r}' returned empty — not live data (#75)")

        # 2) anti-regression: the live catalog must agree with the declared gate class.
        gated_actual = bool(tools[act_op]["annotations"].get("destructiveHint"))
        verdict["gated_actual"] = gated_actual
        if gated_actual != bool(exp["gated"]):
            return fail(f"gate class mismatch: manuscript gated={exp['gated']} but the live "
                        f"catalog marks '{act_op}' destructive={gated_actual} — gate downgraded?")

        before = _read(bridge, read_tool)
        verdict["state_before"] = before

        cap = _AuditCapture()
        audit = logging.getLogger("shal.audit")
        old_level = audit.level
        audit.setLevel(logging.INFO)  # ticket rows are INFO; root's WARNING would drop them
        audit.addHandler(cap)
        try:
            resp = bridge.call(act_tool, act_args)
            if exp["gated"]:
                # the gate MUST intercept: a ticket, and NOTHING sent yet.
                if resp.get("status") != "approval_required" or not resp.get("approval_id"):
                    return fail(f"gated op '{act_op}' did NOT defer for approval (gate "
                                f"bypassed): {resp}")
                approval_id = resp["approval_id"]
                verdict["approval_id"] = approval_id
                verdict["gate_exercised"] = True
                if _read(bridge, read_tool) != before:
                    return fail("device MOVED before approval — pre-I/O leak, gate is unsafe")
                # a SEPARATE gate tool enacts the decision — the operator never self-approves.
                ticket = {"tool": act_tool, "approval_id": approval_id, **resp}
                if decision == "deny":
                    choice = "deny"
                else:
                    choice = approver_fn(ticket)
                    if choice != "approve":
                        # an approve run whose approver refused the EXPECTED op is a harness
                        # failure — the verdict must NOT silently switch to the deny assertion.
                        bridge.call(DENY_TOOL, {"approval_id": approval_id})  # consume the ticket
                        return fail(f"approver refused the expected actuation on an APPROVE run "
                                    f"(returned {choice!r}) — cannot verify a state change")
                bridge.call(APPROVE_TOOL if choice == "approve" else DENY_TOOL,
                            {"approval_id": approval_id})
                # bind the audit proof to THIS ticket (txn == approval_id); an incidental
                # op-layer row carries a different txn and must not satisfy the check.
                verdict["audit_transitions"] = [o for (o, t) in cap.records if t == approval_id]
            else:
                # benign write: it runs directly, no ticket. "deny" is not applicable.
                if decision == "deny":
                    return fail(f"manuscript '{device}' actuation is benign (not gated) — "
                                f"no deny-path to run")
                if not resp.get("ok"):
                    return fail(f"benign write '{act_op}' failed: {resp}")
                choice = "approve"
        finally:
            audit.removeHandler(cap)
            audit.setLevel(old_level)

        # 3) ground truth — pass/fail IS this device read-back, never an opinion.
        after = _read(bridge, read_tool)
        verdict["state_after"] = after
        if choice == "approve":
            if after != exp["becomes"]:
                return fail(f"ground truth failed: after approve, '{exp['read']}' = {after!r}, "
                            f"expected '{exp['becomes']}'")
            if after == before:
                return fail(f"device did NOT move: '{exp['read']}' stayed {after!r} — an approved "
                            f"write must produce a VERIFIED state change")
            want = f"becomes '{exp['becomes']}'"
        else:
            stays = (manuscript.get("deny_path") or {}).get("stays", before)
            if after != stays:
                return fail(f"deny failed: '{exp['read']}' = {after!r}, expected to stay {stays!r}")
            want = f"stays '{stays}'"

        # 4) the audit trail must show THIS ticket's exact transition (gated path only).
        if exp["gated"]:
            want_last = "approved" if choice == "approve" else "denied"
            if verdict["audit_transitions"] != ["requested", want_last]:
                return fail(f"audit trail for ticket {verdict['approval_id']} = "
                            f"{verdict['audit_transitions']}, want ['requested', '{want_last}']")

        # teardown — leave the device safe (harness cleanup, auto-approved AFTER the verdict
        # is decided; this is not the gate under test).
        with shal.approver(shal.AutoApprove()):
            for op, targs in teardown:
                hal.call_tool(tools[op]["name"], targs)

        verdict["verdict"] = "PASS"
        verdict["passed"] = True
        verdict["reason"] = f"{device}: {choice} -> {after!r} ({want})"
        return verdict
    except _ReadFailed as e:
        return fail(f"device read failed — {e}")
    finally:
        hal.close()
