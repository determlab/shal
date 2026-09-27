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
from .routes import RouteSet, last_via
from .transport import Transport

logger = logging.getLogger("shal.loader")
_audit = logging.getLogger("shal.audit")

_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


class Hal:
    def __init__(self, roots: list[Node], ids: dict[str, Node], *,
                 declared_gated: frozenset[str] | None = None,
                 approver=None, source_label: str = "<dict>") -> None:
        self._roots = roots
        self._ids = ids
        self._closed = True  # until every cell is filled: a failed Hal owns nothing
        # this topology's own `policy: {gated: [...]}` (ADR-001 addendum 5b): it
        # belongs to THIS Hal, never to the process. None = the default. What each
        # op ENFORCES is the copy handed below to its driver's bind-time cell,
        # filled exactly once (a second Hal over the same node is a LoadError);
        # `_declared_gated` and `node.hal` are plain references for people,
        # reporting and `conformance` — nothing about the gate depends on them.
        self._declared_gated = declared_gated
        self._source_label = source_label
        # this Hal's own approver (#217), given to `shal.load(approver=)`: it goes
        # into the SAME cells, in the same fill, and is final — there is no way to
        # set it after this. EVERY cell is filled before ANY `node.hal` is
        # published, so a thread waiting for `node.hal` finds the tree already
        # bound; a cell a driver filled first (during `bind()`, or from a thread
        # it started) makes this fill raise, and the load fails.
        bound = None if approver is None else (approver, f"hal:{source_label}")
        for root in roots:
            for node in root.walk():
                bind_hal = getattr(node.driver, "_shal_bind_hal", None)
                if bind_hal is None:
                    continue
                try:
                    bind_hal(self, declared_gated, bound)  # LoadError if filled
                except LoadError:
                    _audit.info("%s: its policy cell was filled before this Hal's; "
                                "load refused", node.path,
                                extra={"event": "audit", "id": node.id or "",
                                       "path": node.path, "outcome": "policy-changed",
                                       "changed": ["gated", "approver"],
                                       "prefilled": True})
                    raise
        for root in roots:
            for node in root.walk():
                node.hal = self
        self._closed = False
        self._tool_idx: dict[str, tuple[Node, str]] | None = None

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
        out = []
        for name, (node, opname) in self._tool_index().items():
            fn = type(node.driver).capability_ops()[opname]
            eff = _effect(fn)
            out.append({"name": name, "device": node.id or node.path,
                        "op": opname, **eff,
                        "annotations": _annotations(eff, _node_gated(node))})
        return out

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Dispatch a tool call by name. Returns {"ok": True, "result": ...} or,
        on failure, {"ok": False, "error": ..., "delivered": ...} — a
        delivery-unknown write is reported, never silently retried (decision 6).

        On a node with ``routes:`` (#236) the result also carries ``via``, the
        route that carried the call (on failure: the route that failed, ``None``
        when every route did), and a failed hop carries ``fix`` (RFC-001
        "Failures the agent must be able to read"). A node without routes has no
        ``via`` key at all, so its results are unchanged."""
        idx = self._tool_index()
        if name not in idx:
            raise LoadError(f"no tool '{name}' (see tool_schemas())")
        node, opname = idx[name]
        method = getattr(node.driver, opname)
        routed = isinstance(getattr(node.driver, "bus", None), RouteSet)
        token = last_via.set(None)
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
            if not routed:
                return {"ok": False, "error": str(e), "delivered": e.delivered}
            # every route failed (3e): the RFC text "<path>: no route delivered — …";
            # else the route that failed is in the text. The fix has its own key
            error = f"{e.path}: {e._msg}" if e.via is None else e._text(with_fix=False)
            return {"ok": False, "error": error, "delivered": e.delivered,
                    "via": e.via, "fix": e.fix}
        except Error as e:
            return {"ok": False, "error": str(e)}
        finally:
            via = last_via.get()
            last_via.reset(token)
        if routed:
            return {"ok": True, "result": result, "via": via}
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

    def _bound_approver(self) -> str | None:
        """The source (``hal:<path>``) of the approver this Hal was loaded with
        (#217), read from its devices' bind-time cells — the ones the op wrapper
        asks — or None when they ask the host's."""
        for root in self._roots:
            for node in root.walk():
                read = getattr(node.driver, "_shal_approver", None)
                got = read() if read is not None else None
                if got is not None:
                    return got[1]
        return None

    def get_gated_effects(self) -> frozenset[str]:
        """The gated set for THIS Hal's devices (ADR-001 addendum 5b): its
        topology's declared ``policy: {gated: [...]}`` (the default if none) ∪ the
        host's widenings (:func:`shal.set_gated_effects`) — the same rule each op
        enforces from the policy it captured at bind."""
        return _effective_gated(self)

    def close(self) -> None:
        """Teardown leaf->root. Deterministic on exit and on exceptions."""
        if self._closed:
            return
        self._closed = True
        for root in self._roots:
            _close_subtree(root, set())
        logger.info("teardown complete", extra={"event": "teardown"})

    def __enter__(self) -> Hal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):  # bare load(): cleanup best-effort, documented as such
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass


def _close_subtree(node: Node, seen: set[int], on_error=None) -> None:
    """Teardown leaf->root of one subtree. With ``on_error(node, exc)`` given,
    each close that raises an ``Exception`` is handed to it and the walk goes on
    (#229); without it (``Hal.close``) the first such error propagates."""
    if id(node) in seen:  # visited-set guard, every walk
        return
    seen.add(id(node))
    for child in node.children.values():
        _close_subtree(child, seen, on_error)
    closers = []
    if node.exposed_bus is not None:
        closers.append(node.exposed_bus.close)
    if isinstance(node.driver, Transport):
        closers.append(node.driver.close)
    for close in closers:
        if on_error is None:
            close()
            continue
        try:
            close()
        except Exception as e:  # noqa: BLE001 — the caller decides
            on_error(node, e)
    if isinstance(node.driver, Transport):
        logger.debug("closed %s", node.path,
                     extra={"event": "teardown", "path": node.path})


def _log_cleanup_failure(node: Node, e: Exception) -> None:
    # the type only: a close message may carry an address (rule 7)
    logger.warning("close after a refused load raised %s at %s",
                   type(e).__name__, node.path,
                   extra={"event": "load_cleanup_failed", "path": node.path})


def _close_unowned(roots: list[Node]) -> None:
    """Close a bound tree that no Hal owns (#229): last root first, each subtree
    leaf->root, every node once. A close that raises is logged with that node's
    path and every other node still closes — it never replaces the error that
    brought us here."""
    seen: set[int] = set()
    for root in reversed(roots):
        _close_subtree(root, seen, _log_cleanup_failure)


def load(source, *, approver=None) -> Hal:
    """Load a topology from a YAML file path or an in-memory mapping (dict).

    The approval policy is the operator's (ADR-001 addendum 5). Driver code runs
    here too (entry-point imports, ``bind``): if it changed the policy, the policy
    is restored and audited — even when the load raises — and the load is a
    ``LoadError``. A topology's top-level ``policy: {gated: [...]}`` belongs to the
    returned Hal alone (addendum 5b): it may loosen the default for this Hal's
    devices, never the host's widenings, and nothing else in the process sees it.
    One ``policy`` audit event records this Hal's effective gated set, the
    approver class and the true ``source`` (``hal:<path>``, ``host`` or
    ``default``), so a narrowing leaves a trace even if no gated call is made.

    ``approver`` (#217) gives THIS Hal its own approver: operator code, given here
    and nowhere else. It fills the same bind-time cells as the declared gated set,
    inside ``Hal.__init__`` before any node is published, and it is final — no
    method sets it after load. Each gated op on this Hal asks it BEFORE the host's
    approver (:func:`shal.set_approver`); every other Hal keeps the host's, so a sim
    rig's :class:`~shal.AutoApprove` never approves a Hal loaded from a real file.
    It is not a ContextVar: it holds in every thread. A cell a driver filled first
    makes the load fail (``LoadError``, audited). ``shal mcp`` refuses a Hal that
    carries one: under an MCP host the ticket is the only approver. Approval
    records and the ``policy`` event name the approver's source: ``hal:<path>``,
    ``host`` or ``default``."""
    from .approval import get_approver
    if approver is not None and (isinstance(approver, type)
                                 or not callable(getattr(approver, "approve", None))):
        raise TypeError(f"approver= needs an Approver (an instance with "
                        f"approve(request) -> bool), not {approver!r}")
    label = "<dict>" if isinstance(source, Mapping) else str(source)
    _driver._refuse_rebound_names(label)  # a name rebound since import: refuse
    before = _driver._policy_snapshot()
    try:
        roots, ids, policy = load_tree(source)
    except BaseException:
        _refuse_load_change(before, raising=False)  # restored + audited, then re-raise
        raise
    # Until a Hal exists, the bound tree belongs to no one. ANY error here — a
    # refused policy change (#229), a bad policy.gated, a fill error (#217) —
    # closes it ONCE and re-raises the original exception. A failed Hal stays
    # `_closed`, so its __del__ closes nothing on top of this.
    try:
        _refuse_load_change(before)
        declared = None
        if policy and "gated" in policy:
            try:
                declared = _driver._coerce_gated(policy["gated"], operator=True)
            except (TypeError, ValueError) as e:  # schema catches these first
                raise LoadError(f"{label}: policy.gated: {e}") from e
        # the ONE write of the policy — the approver included (#217)
        hal = Hal(roots, ids, declared_gated=declared, approver=approver,
                  source_label=label)
    except BaseException:
        _close_unowned(roots)
        raise
    widened = sorted(_driver.get_gated_effects() - _driver._canon()[2])
    source_of = (f"hal:{label}" if hal._declared_gated is not None
                 else "host" if widened else "default")
    gated = sorted(hal.get_gated_effects())
    if approver is not None:
        approver_name, approver_source = type(approver).__name__, f"hal:{label}"
    else:
        approver_name = type(get_approver()).__name__
        approver_source = "default" if _driver._canon()[1].get() is None else "host"
    _audit.info("approval policy: gated %s, approver %s (%s)", gated, approver_name,
                source_of,
                extra={"event": "policy", "gated": gated, "approver": approver_name,
                       "source": source_of, "widened": widened,
                       "approver_source": approver_source})
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


def _node_gated(node: Node) -> frozenset[str]:
    """The gated set a node's ops ENFORCE — read from the same bind-time capture
    the op wrapper uses, so every advertiser (the MCP ``destructiveHint``, the tool
    description, ``shal call``) says exactly what the gate does (advertised ==
    enforced, per Hal; ADR-001 addendum 5b)."""
    gated = getattr(node.driver, "_shal_gated", None)
    return gated() if gated is not None else _effective_gated(getattr(node, "hal", None))


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
                 if eff["side_effect"] in _node_gated(node) else "")
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
