"""Lookup API + lifecycle (DESIGN V2 'Lookup API' / 'Lifecycle')."""
from __future__ import annotations

import logging
import re
from collections.abc import Mapping

from . import driver as _driver
from . import limits
from .driver import _effective_gated, inferred_side_effect
from .errors import ApprovalDenied, Error, HopError, LimitError, LoadError
from .loader import load_tree
from .node import Node
from .transport import Transport

logger = logging.getLogger("shal.loader")
_audit = logging.getLogger("shal.audit")

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


class Hal:
    def __init__(self, roots: list[Node], ids: dict[str, Node], *,
                 declared_gated: frozenset[str] | None = None) -> None:
        self._roots = roots
        self._ids = ids
        self._closed = False
        # this topology's own `policy: {gated: [...]}` (ADR-001 addendum 5b): it
        # belongs to THIS Hal, never to the process. None = the default. Passed in
        # by shal.load and WRITE-ONCE (see __setattr__); each node gets a set-once
        # back-reference so the op wrapper finds it.
        object.__setattr__(self, "_declared_gated", declared_gated)
        self._source_label = "<dict>"
        for root in roots:
            for node in root.walk():
                node.hal = self
        self._tool_idx: dict[str, tuple[Node, str]] | None = None

    def __setattr__(self, name: str, value) -> None:
        # the topology's declared gated set is written ONCE, at construction by
        # shal.load; any later assignment — a driver op, a thread it started, or
        # host code — raises (ADR-001 addendum 5/5b). Python is not a sandbox:
        # object.__setattr__ bypasses this; the op wrapper's snapshot still
        # catches a change made inside a call.
        if name == "_declared_gated":
            _driver._refuse_write_once("Hal._declared_gated",
                                       self.__dict__.get("_source_label", "<hal>"))
        object.__setattr__(self, name, value)

    def __delattr__(self, name: str) -> None:
        # deleting the declared set would drop the Hal back to the default (a
        # widening topology loosened) — refused the same way as reassigning it
        if name == "_declared_gated":
            _driver._refuse_write_once("Hal._declared_gated",
                                       self.__dict__.get("_source_label", "<hal>"))
        object.__delattr__(self, name)

    # -- lookup (topology immutable after load -> lock-free) -----------------
    def get_device(self, key: str | None = None, *,
                   id: str | None = None, path: str | None = None):
        if sum(x is not None for x in (key, id, path)) != 1:
            raise LoadError("get_device takes exactly one of: positional key, id=, path=")
        if key is not None:  # DECISIONS v2.1 #2: leading '/' = path, else id
            if key.startswith("/"):
                path = key
            else:
                id = key
        node = self._ids.get(id) if id is not None else self._by_path(path)  # type: ignore[arg-type]
        if node is None:
            raise LoadError(f"no device with {'id' if id else 'path'} "
                            f"'{id if id is not None else path}'")
        if node.driver is None:
            raise LoadError(f"{node.path} has no driver (channel node?)")
        return node.driver

    def get_node(self, id: str) -> Node:
        node = self._ids.get(id)
        if node is None:
            raise LoadError(f"no node with id '{id}'")
        return node

    # -- LLM tool surface (DESIGN V2 'agent bus') ----------------------------
    def _tool_index(self) -> dict[str, tuple[Node, str]]:
        """tool name -> (device node, op name). Built once from the bound tree."""
        if getattr(self, "_tool_idx", None) is None:
            idx: dict[str, tuple[Node, str]] = {}
            for root in self._roots:
                for node in root.walk():
                    drv = node.driver
                    # devices are agent-callable; a bus provides transport, not
                    # capabilities — exclude it (same rule as the audit channel)
                    if drv is None or isinstance(drv, Transport):
                        continue
                    if not node.exposed:  # `expose: false` -> off the agent surface
                        continue
                    handle = node.id or _NAME_SAFE.sub("_", node.path.lstrip("/"))
                    for opname in type(drv).capability_ops():
                        idx[f"{handle}__{opname}"] = (node, opname)
            self._tool_idx = idx
        return self._tool_idx

    def tool_schemas(self) -> list[dict]:
        """Anthropic tool-use definitions ({name, description, input_schema}) for
        every capability op on every device — drive a SHAL tree from an LLM."""
        out = []
        for name, (node, opname) in self._tool_index().items():
            fn = type(node.driver).capability_ops()[opname]
            # the BOUND effective schema (class ⊕ op_limits ⊕ config.limits) when
            # available — advertised == enforced, per node (issue #10)
            bound = getattr(node.driver, "_op_schemas", {}).get(opname)
            out.append({
                "name": name,
                "description": _describe(node, opname, fn),
                "input_schema": bound or _params_schema(fn),
            })
        return out

    def tool_catalog(self) -> list[dict]:
        """Richer per-tool facts for policy/gating: side_effect + idempotency.
        Pair with tool_schemas() — the harness gates writes/actuators, not reads."""
        gated = self.get_gated_effects()  # this Hal's set: advertised == enforced
        out = []
        for name, (node, opname) in self._tool_index().items():
            fn = type(node.driver).capability_ops()[opname]
            eff = _effect(fn)
            out.append({"name": name, "device": node.id or node.path,
                        "op": opname, **eff,
                        "annotations": _annotations(eff, gated)})
        return out

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Dispatch a tool call by name. Returns {"ok": True, "result": ...} or,
        on failure, {"ok": False, "error": ..., "delivered": ...} — a
        delivery-unknown write is reported, never silently retried (decision 6)."""
        idx = self._tool_index()
        if name not in idx:
            raise LoadError(f"no tool '{name}' (see tool_schemas())")
        node, opname = idx[name]
        method = getattr(node.driver, opname)
        try:
            result = method(**(arguments or {}))
        except LimitError as e:
            # structured refusal: nothing was sent (no `delivered` key on purpose) —
            # the violations let an agent self-correct in one step (issue #10)
            return {"ok": False, "error": str(e), "rejected": "limits",
                    "violations": e.violations}
        except ApprovalDenied as e:
            # human-in-the-loop refusal: pre-I/O, nothing sent (no `delivered`
            # key) — distinct from a limit rejection so an agent can tell why
            # it was stopped and route to a human (issue #14). `reason` is
            # "no-approver" when no one could be asked, else None (#186)
            return {"ok": False, "error": str(e), "rejected": "approval",
                    "op": e.op, "device": node.id or node.path, "reason": e.reason}
        except HopError as e:
            return {"ok": False, "error": str(e), "delivered": e.delivered}
        except Error as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "result": result}

    def _by_path(self, path: str) -> Node | None:
        parts = [p for p in path.split("/") if p]
        if not parts:
            return None
        node = next((r for r in self._roots if r.name == parts[0]), None)
        for part in parts[1:]:
            if node is None:
                return None
            node = node.children.get(part)
        return node

    # -- lifecycle ------------------------------------------------------------
    def warm(self) -> list[tuple[str, Exception]]:
        """Eagerly activate every transport so the FIRST read/tool call doesn't pay the
        connect/login latency. Lazy activation is correct for a script, but over MCP that
        first-call latency shows up as a hang-then-warm (the client times out, the bus
        finishes connecting, the next call is fine) — issue #83. Warming at serve-start
        moves the cost before the first request. Best-effort: returns ``(path, error)`` for
        any bus that failed to activate, so the caller can warn and still serve the devices
        that came up (one offline bus must not sink the whole server)."""
        failures: list[tuple[str, Exception]] = []
        seen: set[int] = set()
        for root in self._roots:
            for node in root.walk():
                drv = node.driver
                if isinstance(drv, Transport) and id(drv) not in seen:
                    seen.add(id(drv))
                    try:
                        drv.ensure_ready()
                    except Exception as e:  # noqa: BLE001 — report, never crash the warm-up
                        failures.append((node.path, e))
        return failures

    def get_gated_effects(self) -> frozenset[str]:
        """The gated set for THIS Hal's devices (ADR-001 addendum 5b): its
        topology's declared ``policy: {gated: [...]}`` (the default if none) ∪ the
        host's widenings (:func:`shal.set_gated_effects`). What the gate enforces
        and what this Hal's tool surface advertises."""
        return _effective_gated(self)

    def close(self) -> None:
        """Teardown leaf->root. Deterministic on exit and on exceptions."""
        if self._closed:
            return
        self._closed = True
        for root in self._roots:
            self._close_subtree(root, set())
        logger.info("teardown complete", extra={"event": "teardown"})

    def _close_subtree(self, node: Node, seen: set[int]) -> None:
        if id(node) in seen:  # visited-set guard, every walk
            return
        seen.add(id(node))
        for child in node.children.values():
            self._close_subtree(child, seen)
        if node.exposed_bus is not None:
            node.exposed_bus.close()
        if isinstance(node.driver, Transport):
            node.driver.close()
            logger.debug("closed %s", node.path,
                         extra={"event": "teardown", "path": node.path})

    def __enter__(self) -> Hal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):  # bare load(): cleanup best-effort, documented as such
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass


def load(source) -> Hal:
    """Load a topology from a YAML file path or an in-memory mapping (dict).

    The approval policy is the operator's (ADR-001 addendum 5). Driver code runs
    here too (entry-point imports, ``bind``): if it changed the policy, the policy
    is restored and audited — even when the load raises — and the load is a
    ``LoadError``. A topology's top-level ``policy: {gated: [...]}`` belongs to the
    returned Hal alone (addendum 5b): it may loosen the default for this Hal's
    devices, never the host's widenings, and nothing else in the process sees it.
    One ``policy`` audit event records this Hal's effective gated set, the
    approver class and the true ``source`` (``hal:<path>``, ``host`` or
    ``default``), so a narrowing leaves a trace even if no gated call is made."""
    from .approval import get_approver
    label = "<dict>" if isinstance(source, Mapping) else str(source)
    before = _driver._policy_snapshot()
    try:
        roots, ids, policy = load_tree(source)
    except BaseException:
        _refuse_load_change(before, raising=False)  # restored + audited, then re-raise
        raise
    _refuse_load_change(before)
    declared = None
    if policy and "gated" in policy:
        try:
            declared = _driver._coerce_gated(policy["gated"], operator=True)
        except (TypeError, ValueError) as e:  # schema catches these first; belt+braces
            Hal(roots, ids).close()
            raise LoadError(f"{label}: policy.gated: {e}") from e
    hal = Hal(roots, ids, declared_gated=declared)  # the ONE write of the policy
    hal._source_label = label
    widened = sorted(_driver.get_gated_effects() - _driver._DEFAULT_GATED)
    source_of = (f"hal:{label}" if hal._declared_gated is not None
                 else "host" if widened else "default")
    gated = sorted(hal.get_gated_effects())
    approver = type(get_approver()).__name__
    _audit.info("approval policy: gated %s, approver %s (%s)", gated, approver,
                source_of,
                extra={"event": "policy", "gated": gated, "approver": approver,
                       "source": source_of, "widened": widened})
    return hal


def _refuse_load_change(before, *, raising: bool = True) -> None:
    """Driver code that ran during the load (entry-point imports, ``bind``) must not
    have changed the policy: restore it and audit the attempt — even when the load
    is failing anyway — and raise ``LoadError`` when ``raising``."""
    changed = _driver._policy_changed(before)
    if not changed:
        return
    _driver._restore_policy(before)
    _audit.info("a driver changed the approval policy while loading (%s); refused",
                ", ".join(changed),
                extra={"event": "audit", "outcome": "policy-changed",
                       "changed": changed})
    if raising:
        raise LoadError("a driver changed the approval policy while loading "
                        f"({', '.join(changed)}); only the operator sets it")


# -- LLM tool-schema helpers ----------------------------------------------------

def _effect(fn) -> dict:
    """side_effect + idempotency for an op: explicit @shal.op wins, else inferred
    fail-closed as 'actuator' (driver.inferred_side_effect). @idempotent is only
    about retry, never the label (#194)."""
    meta = getattr(fn, "__shal_op__", None) or {}
    idem = bool(getattr(fn, "__shal_idempotent__", False))
    side = inferred_side_effect(fn)
    return {"side_effect": side, "idempotent": idem, "unit": meta.get("unit")}


def _annotations(eff: dict, gated: frozenset[str]) -> dict:
    """Map SHAL's side_effect/idempotency onto MCP tool-annotation hint names so
    agent harnesses recognize them (issue #1). ``gated`` is the effective set of
    the Hal whose tool this is (ADR-001 addendum 5b)."""
    side = eff["side_effect"]
    return {"readOnlyHint": side == "none",
            "idempotentHint": eff["idempotent"],
            # destructive == gated by the approval interlock. The LIVE set of THIS
            # Hal (issues #114, 5b), never the shipped default: the tool surface must
            # not tell an agent a call is free while the gate stops it for a human.
            "destructiveHint": side in gated}


def _describe(node: Node, opname: str, fn) -> str:
    meta = getattr(fn, "__shal_op__", None) or {}
    eff = _effect(fn)
    doc_first = (fn.__doc__ or "").strip().splitlines()[0].strip() if fn.__doc__ else ""
    base = meta.get("description") or doc_first or f"Invoke '{opname}'."
    parts = [base]
    if node.description:  # instance context from the topology (issue #1)
        parts.append(node.description)
    parts.append(f"Device '{node.id or node.path}' at {node.path}.")
    if eff["unit"]:
        parts.append(f"Unit: {eff['unit']}.")
    # keyed on the LABEL, not @idempotent (#194): advertised == enforced
    if eff["side_effect"] == "none" and eff["idempotent"]:
        parts.append("Idempotent read — safe to call repeatedly.")
    elif eff["idempotent"]:
        gated = (" It needs a person's approval."
                 if eff["side_effect"] in _effective_gated(getattr(node, "hal", None))
                 else "")
        parts.append(f"Side effect ({eff['side_effect']}): safe to re-send; a lost "
                     f"delivery is retried once.{gated}")
    else:
        parts.append(f"Side effect ({eff['side_effect']}): a failed call may have "
                     f"partially applied and is NOT auto-retried — confirm before re-calling.")
    return " ".join(parts)


def _params_schema(fn) -> dict:
    """JSON Schema for an op's parameters: the type-hint skeleton merged with the
    op's declared limits (issue #10). ONE artifact — what the model is shown here
    is byte-for-byte what the framework enforces before any bus I/O."""
    return limits.merged_params_schema(fn)
