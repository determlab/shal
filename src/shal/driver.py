"""Driver base + @idempotent + bind-time capability wrapping (DECISIONS v2.1 #4).

Retry policy (DESIGN V2 decision 6): idempotent ops reconnect once / retry once;
a write is NEVER silently re-fired — HopError(delivered=...) propagates untouched.
The framework, not the driver, implements the retry machinery.

Observability (DESIGN V2 'Logging'): the wrapper is the single instrumentation
point for capability calls — txn assignment, DEBUG call traces, WARNING on the
handled retry, a DEBUG breadcrumb when raising (so a log-only reader sees the
failure; the ERROR-level raise-or-log rule is untouched), and the shal.audit
record for write ops on device drivers.
"""
from __future__ import annotations

import functools
import inspect
import logging
import time
from collections.abc import Callable, Iterable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any

from . import log as _log
from .errors import Error as _ShalError
from .errors import HopError
from .errors import LoadError as _LoadError

if TYPE_CHECKING:
    from .node import Node
    from .transport import Transport

_audit = logging.getLogger("shal.audit")


def idempotent(fn: Callable) -> Callable:
    """Mark a capability op as safe to auto-retry across transient drops."""
    fn.__shal_idempotent__ = True
    return fn


_SIDE_EFFECTS = frozenset({"none", "write", "actuator", "config"})
# fail-closed default (issue #19, #194): an un-annotated op infers "actuator"
# (gated, audited), never "write" or "none" — whether or not it is @idempotent. A
# forgotten side_effect must not silently reach hardware. Authors opt DOWN to
# "write" (benign, ungated) or "none" (a read) explicitly.

# -- which effects require human-in-the-loop approval (issue #114) -------------
# The SHIPPED default (issue #14): physical motion ("actuator") and destructive /
# configuration writes ("config"). A plain "write" (a benign setpoint/register) is
# audited but NOT gated. Like the Approver next door, SHAL ships the *mechanism*
# plus a *safe default* and the operator seats the *policy*:
#
#     import shal
#     shal.set_gated_effects({"write", "actuator", "config"})    # stricter rig
#     with shal.gated_effects({"write", "actuator", "config"}):  # scoped policy
#         ...
#
# A consumer that never calls the API sees byte-identical behaviour. The gated set
# decides only which calls ASK; the audit follows the LABEL (#194) and does not
# move with it.
#
# WHO may set it (ADR-001 addendum 5 + 5b): the gated set and the Approver are
# ONE policy, and it is the operator's — a driver never sets it:
#   * The HOST may only WIDEN: the process context below holds only what the host
#     sets, a superset of the default (its widenings are what it adds to it).
#   * A TOPOLOGY's `policy: {gated: [...]}` belongs to the Hal that loaded it
#     (`Hal._declared_gated`) — it may loosen the default for that Hal's own
#     devices and nothing else. The wrapper reads it through `node.hal`.
#   * EFFECTIVE set for an op = (its Hal's declared set, or the default)
#     ∪ (the host's widenings) — see `_effective_gated`. Two Hals gate
#     independently; closing one has nothing to reset.
#   * Driver code is checked where it runs: a `--drivers` import
#     (mcp.server._import_drivers -> LoadError), binding at load (shal.load ->
#     LoadError), and every op call (the wrapper restores the policy, audits
#     outcome "policy-changed", and raises shal.Error).
_DEFAULT_GATED: frozenset[str] = frozenset({"actuator", "config"})
_current_gated: ContextVar[frozenset[str] | None] = ContextVar("shal_gated", default=None)


def _coerce_gated(effects: Iterable[str], *, operator: bool = False) -> frozenset[str]:
    """Validate a candidate gated set AT THE CALL SITE — an unknown or meaningless
    effect name must fail where the host wrote it, not silently at the next op.

    A bare ``str`` is a ``TypeError``: ``set_gated_effects("write")`` would iterate
    its characters, and ``""`` would silently gate nothing. ``"none"`` is rejected
    outright: it marks a READ, and "stop the world and ask a human before this
    read" is not a thing the gate can mean (D6: reads are free and human-runnable).
    Gating it would also make the advertised hints self-contradictory — the same
    op would carry ``readOnlyHint: true`` and ``destructiveHint: true``. Unless
    ``operator`` (a topology's ``policy:``, read by ``shal.load`` for its own Hal),
    the set must be a superset of the default: the host may only widen."""
    if isinstance(effects, (str, bytes)):
        raise TypeError(
            f"gated effects must be a set of side_effect names, not a string "
            f"({effects!r}); e.g. {{'write', 'actuator', 'config'}}")
    given = frozenset(effects)
    if "none" in given:
        raise ValueError(
            "gated effects cannot include 'none': it marks a read, and gating a "
            "read has no meaning (it would advertise readOnlyHint and "
            "destructiveHint together). Gate 'write'/'actuator'/'config'.")
    unknown = sorted(str(e) for e in given if e not in _SIDE_EFFECTS)
    if unknown:
        raise ValueError(
            f"unknown side_effect(s) {unknown}: gated effects must be a subset of "
            f"{sorted(_SIDE_EFFECTS - {'none'})}")
    dropped = sorted(_DEFAULT_GATED - given)
    if dropped and not operator:
        raise ValueError(
            f"gated effects {sorted(given)} drop {dropped} from the default "
            f"{sorted(_DEFAULT_GATED)}: the host may only widen the gate. A topology "
            f"may loosen it for its own devices (policy: {{gated: [...]}}).")
    return given


def get_gated_effects() -> frozenset[str]:
    """The HOST-level gated set for the current context: the default
    (``{"actuator", "config"}``) plus the host's widenings. A Hal whose topology
    declares ``policy: {gated: [...]}`` gates its own devices by
    :meth:`shal.Hal.get_gated_effects` instead (its set ∪ these widenings)."""
    got = _current_gated.get()
    return _DEFAULT_GATED if got is None else got


def _effective_gated(hal=None) -> frozenset[str]:
    """The ONE rule (ADR-001 addendum 5b): the gated set for an op on ``hal`` =
    (the Hal's declared topology set, or the default) ∪ (the host's widenings).
    Read by the gate, the audit's ``gated`` field, and every advertiser (the MCP
    ``destructiveHint``, the tool description, ``shal call``) — advertised ==
    enforced, per Hal. With no Hal (the class catalog) it is the host-level set."""
    host = get_gated_effects()
    declared = getattr(hal, "_declared_gated", None)
    if declared is None:
        return host
    return declared | (host - _DEFAULT_GATED)


def set_gated_effects(effects: Iterable[str]) -> Token:
    """Install ``effects`` as the active gated set. Returns a token for
    :func:`reset_gated_effects`. Raises at the call: ``TypeError`` for a bare
    string; ``ValueError`` for an unknown name, for ``"none"``, or for a set that
    drops ``"actuator"``/``"config"`` — the host may only widen; a topology's
    ``policy: {gated: [...]}`` may loosen the default for its own Hal (ADR-001
    addendum 5b). The widening applies to every Hal. Never call it from
    driver code: a ``--drivers`` import or an op that changes the policy is refused.

    Note: the policy lives in a :class:`~contextvars.ContextVar`. A newly spawned
    raw OS thread does NOT inherit the caller's context, so it falls back to the
    safe default (``{"actuator", "config"}``). ``asyncio`` tasks and
    ``anyio.to_thread`` workers (``shal mcp``'s dispatch) DO inherit it. Seat it
    BEFORE the tool list is served (MCP ``list_tools``): the advertised hints are
    computed then, the gate on every call. Pair it with :func:`shal.set_approver`
    — see ``shal.approval``."""
    return _current_gated.set(_coerce_gated(effects))


def reset_gated_effects(token: Token) -> None:
    """Undo a :func:`set_gated_effects`, restoring the previous gated set."""
    _current_gated.reset(token)


@contextmanager
def gated_effects(effects: Iterable[str]):
    """Scope a gated set to a ``with`` block; the previous set is restored on exit.
    Same rules as :func:`set_gated_effects` (widen only)."""
    chosen = _coerce_gated(effects)  # raise at the `with`, before the block runs
    token = _current_gated.set(chosen)
    try:
        yield chosen
    finally:
        _current_gated.reset(token)


# -- tampering by driver code is loud, not trusted (ADR-001 addendum 5) ---------
# The policy is (raw host gated set, raw Approver, the two module DEFAULTS the raw
# values fall back to — rebinding `_DEFAULT_GATED` or `approval._DEFAULT` changes
# the effective policy too — and, for an op, its Hal's declared topology set).
# Python is not a sandbox: comparing a snapshot taken before driver code runs with
# one taken after makes a change structural and visible instead of silent.
_PolicySnap = tuple  # (gated, Approver, default gated, default Approver, node,
#                       node's Hal, that Hal's declared set)
# the ENCLOSING op's (snapshot, driver, op, txn), so a nested op call (a driver op
# that calls another device) catches a change made before it — and names the
# enclosing op that made it, not the innocent inner one
_op_policy: ContextVar[tuple | None] = ContextVar("shal_op_policy", default=None)


def _policy_snapshot(node=None) -> _PolicySnap:
    from . import approval
    hal = getattr(node, "hal", None)
    return (_current_gated.get(), approval._current.get(), _DEFAULT_GATED,
            approval._DEFAULT, node, hal, getattr(hal, "_declared_gated", None))


def _policy_changed(snap: _PolicySnap) -> list[str]:
    """Which halves differ from ``snap`` now: [] if none, else "gated"/"approver".
    For an op the node's Hal is compared by IDENTITY too (``node.hal is`` the Hal it
    had) — swapping in another object with a looser declared set is a change —
    and the declared set is read from the CAPTURED Hal."""
    gated, appr, dgated, dappr, node, hal, _ = _policy_snapshot(snap[4])
    declared = getattr(snap[5], "_declared_gated", None)
    return [name for name, same in (
        ("gated", gated == snap[0] and dgated is snap[2] and hal is snap[5]
         and declared is snap[6]),
        ("approver", appr is snap[1] and dappr is snap[3])) if not same]


def _restore_policy(snap: _PolicySnap) -> None:
    global _DEFAULT_GATED
    from . import approval
    _current_gated.set(snap[0])
    approval._current.set(snap[1])
    _DEFAULT_GATED = snap[2]
    approval._DEFAULT = snap[3]
    # the write-once guards on Node.hal / Hal._declared_gated refuse a plain set,
    # so putting the operator's values BACK goes around them (restoring, not
    # changing)
    if snap[4] is not None:
        object.__setattr__(snap[4], "hal", snap[5])
    if snap[5] is not None:
        object.__setattr__(snap[5], "_declared_gated", snap[6])


def _refuse_write_once(what: str, path: str) -> None:
    """The write-once guards on ``Node.hal`` / ``Hal._declared_gated`` refused an
    assignment: audit the attempt (outcome ``policy-changed``) and raise
    ``AttributeError`` (ADR-001 addendum 5/5b). Called from the guards, so it is
    on the record whether an op, a thread it started, or host code tried."""
    _audit.info("%s: refused an assignment to %s (write-once)", path, what,
                extra={"event": "audit", "path": path, "outcome": "policy-changed",
                       "changed": ["gated"], "attribute": what,
                       "txn": _log.current_txn.get()})
    raise AttributeError(
        f"{path}: {what} is set once by shal.load and cannot be reassigned — the "
        f"approval policy is the operator's, never a driver's (ADR-001 addendum 5b). "
        f"The host widens with shal.set_gated_effects.")


def _refuse_import_change(before: _PolicySnap, module: str, file: str = "") -> None:
    """After importing driver code (``--drivers``, ``shal check module:Class``): if
    the import changed the policy, restore it, audit the attempt and raise
    ``LoadError("<module> changed the approval policy at import")``."""
    changed = _policy_changed(before)
    if not changed:
        return
    _restore_policy(before)
    _audit.info("%s changed the approval policy at import (%s); refused",
                module, ", ".join(changed),
                extra={"event": "audit", "outcome": "policy-changed",
                       "file": file or module, "changed": changed})
    raise _LoadError(f"{module} changed the approval policy at import")


def op(description: str, *, unit: str | None = None,
       side_effect: str | None = None,
       params: dict[str, dict] | None = None) -> Callable:
    """Attach LLM-facing metadata to a capability op (DESIGN V2 'agent bus').

    `description` should say WHEN to call it, not just what it does — that is what
    a model keys on. `side_effect` is "none" (a read), "write" (a benign state
    change), "actuator" (physical motion), or "config" (a destructive/
    configuration write); if omitted it is inferred FAIL-CLOSED (issue #19, #194):
    the op is treated as "actuator" (gated, audited), @idempotent or not —
    declare "none" for a read and "write" for a benign, ungated state change.
    "actuator" and "config" ops are gated by the approval interlock (issue #14) — they stop
    for the active Approver before any bus I/O. WHICH effects are gated is itself a
    policy (issue #114): the default is `{"actuator", "config"}`; a host may only
    widen it (`shal.set_gated_effects` / `shal.gated_effects`), and a topology's
    `policy: {gated: [...]}` loosens the default only for its own Hal. The metadata
    feeds `hal.tool_schemas()` and is required on every public op of a driver that
    sets `llm_ready = True` (checked at bind — fail loudly, never at call time).

    `params` (issue #10) maps a parameter name to a JSON-Schema fragment
    (minimum/maximum/enum/...) declaring its SAFE OPERATING LIMITS. One schema,
    two trust layers: it is advertised verbatim in `tool_schemas()` AND enforced
    by the framework before any bus I/O (a violation raises `shal.LimitError`;
    the device never sees the command). The driver body stays check-free.
    """
    if side_effect is not None and side_effect not in _SIDE_EFFECTS:
        raise ValueError(f"side_effect must be one of {sorted(_SIDE_EFFECTS)}")

    def deco(fn: Callable) -> Callable:
        if params:  # loud at decoration: fragment keys must name real params
            import inspect
            names = set(inspect.signature(fn).parameters) - {"self"}
            unknown = set(params) - names
            if unknown:
                raise ValueError(
                    f"@op on {fn.__qualname__}: params {sorted(unknown)} do not "
                    f"name parameters of the op (has: {sorted(names)})")
        fn.__shal_op__ = {"description": description, "unit": unit,
                          "side_effect": side_effect, "params": params or None}
        return fn
    return deco


def inferred_side_effect(fn: Callable) -> str:
    """The effective side_effect of an op — the SINGLE source of truth for the
    gate, the audit, and the advertised tool hints (advertised == enforced).

    Explicit `@op(side_effect=...)` wins. Otherwise it is inferred FAIL-CLOSED
    (issue #19; ADR-001 addendum 4, #194): an un-annotated op is "actuator"
    (gated, audited) whether or not it is `@idempotent` — `@idempotent` says only
    that a lost-delivery retry is safe, never that the op is a read. So a
    forgotten annotation stops for approval rather than silently reaching
    hardware. Authors opt DOWN to "none" (a read) or "write" (a benign, ungated
    state change) explicitly."""
    declared = (getattr(fn, "__shal_op__", None) or {}).get("side_effect")
    if declared:
        return declared
    return "actuator"


class Driver:
    compatible: str = ""
    kind: type | None = None  # transport kind required of the parent bus
    llm_ready: bool = False   # opt-in: require @shal.op metadata on every op (bind-time)

    # framework-injected (bind time):
    node: Node
    bus: Transport | None
    addr: Any
    log: _log._ShalLogAdapter

    def bind(self, node: Node) -> None:
        self.node = node
        self.bus = node.parent_bus
        self.addr = node.address
        if getattr(self, "log", None) is None:
            # bus classes already bound a shal.bus.<family> logger in __init__;
            # pure device drivers get the shal.driver.<compatible> one here
            self.log = _log.driver_logger(self.compatible, node.path, node.id)
        self._wrap_capabilities()

    def safe_state(self) -> None:  # actuator contract hook (Phase 2 watchdog)
        pass

    def op_limits(self) -> dict[str, dict[str, dict]]:
        """Optional bind-time narrowing of declared limits: {op: {param: JSON-Schema
        fragment}}, for ADDRESS-DEPENDENT ratings the class decorator cannot know
        (e.g. a PSU whose channel 3 is the 5 V rail). May only TIGHTEN the @op
        declaration — widening is a LoadError at bind. Default: nothing."""
        return {}

    @classmethod
    def authoring_meta(cls) -> dict:
        """Authoring metadata for ``shal.catalog()`` (issue #1). The catalog DERIVES
        everything it can (compatible, kind, kinds(), capability, ops, summary); a
        class only declares the irreducible bits here as JSON-Schema fragments:
        ``address_schema`` (this node's address grammar), ``config_schema``, and for
        buses optionally ``child_address_schema``. Default: nothing extra."""
        return {}

    def provide_child_bus(self, child: Node) -> Transport | None:
        """A driver that exposes a distinct bus per child (mux channels)
        returns it here; None means 'use this driver if it is a Transport'."""
        return None

    # -- bind-time wrapping: txn id on every capability call; retry iff idempotent
    _PLUMBING = frozenset({
        "bind", "safe_state", "kinds", "provide_child_bus", # Driver/Transport API
        "op_limits",                                        # limits hook (issue #10)
        "txn", "run", "exchange", "subscribe",              # transport kind methods
        "validate_address", "activate", "ensure_ready", "is_active", "close",
    })

    @classmethod
    def capability_ops(cls) -> dict[str, Callable]:
        """The public capability methods this driver defines (the set the
        framework wraps, audits, and exposes as LLM tools) — name -> raw function."""
        ops: dict[str, Callable] = {}
        for name in dir(cls):
            if name.startswith("_") or name in cls._PLUMBING:
                continue
            fn = getattr(cls, name, None)
            if not callable(fn):
                continue
            unwrapped = getattr(fn, "__wrapped__", fn)
            if name in Driver.__dict__ or not _is_capability(unwrapped):
                continue
            ops[name] = unwrapped
        return ops

    def _wrap_capabilities(self) -> None:
        from . import limits as _limits  # local: avoid import cycle at module load
        ops = type(self).capability_ops()
        if self.llm_ready:  # opt-in conformance: every op must carry @shal.op
            missing = [n for n, fn in ops.items()
                       if not getattr(fn, "__shal_op__", None)
                       or not fn.__shal_op__.get("description")]
            if missing:
                raise _LoadError(
                    f"{self.compatible}: llm_ready driver is missing @shal.op "
                    f"metadata on: {', '.join(sorted(missing))}")
        # limits layers may only reference real ops — fail loudly at bind
        for where, mapping in ((f"{self.compatible}.op_limits()", self.op_limits() or {}),
                               (f"{self.node.path}: config.limits",
                                _limits.config_limits(self.node))):
            unknown = set(mapping) - set(ops)
            if unknown:
                raise _LoadError(f"{where}: {sorted(unknown)} do not name "
                                 f"capability ops (has: {sorted(ops)})")
        self._op_schemas: dict[str, dict] = {}  # effective, advertised == enforced
        for name, fn in ops.items():
            if not getattr(getattr(type(self), name), "__shal_wrapped__", False):
                setattr(self, name, self._make_call(fn))

    def _make_call(self, fn: Callable) -> Callable:
        from . import limits as _limits  # local: avoid import cycle at module load
        from .transport import Transport
        retry = getattr(fn, "__shal_idempotent__", False)
        op = fn.__name__
        # side_effect is fail-closed by default (see inferred_side_effect)
        side_effect = inferred_side_effect(fn)
        # audit follows the LABEL, not @idempotent (ADR-001 addendum 4, #194):
        # every non-read op on a DEVICE driver is audited — an idempotent write
        # included; reads are not, and a bus's public helpers are not device
        # commands
        audited = side_effect != "none" and not isinstance(self, Transport)
        # operating limits: effective schema (class ⊕ op_limits() ⊕ config.limits)
        # compiled ONCE at bind, checked on every call BEFORE the op body — the
        # only path to bus I/O (issue #10)
        schema, constrained = _limits.effective_schema(self, fn, op)
        self._op_schemas[op] = schema
        guard = (_limits.Guard(fn, schema, path=self.node.path, opname=op)
                 if constrained else None)
        # human-in-the-loop gate (issue #14): gated effects (default actuator/config)
        # on device drivers only — a bus provides transport, not actuation (same rule
        # as audit). BOTH halves of the decision are resolved at CALL time, so a host
        # can inject an Approver AND a gated set after load (issue #114). That is not
        # just convenience: the advertised `destructiveHint` (hal._annotations) is
        # computed when tool_catalog() is called, so a bind-time gated set would let
        # the advertisement and the enforcement diverge under a seated policy. The
        # Transport exclusion is a fixed property of this driver, so it stays at bind.
        # `audited` above follows the LABEL, never this policy (#194).
        gatable = not isinstance(self, Transport)
        sig = inspect.signature(fn) if gatable else None

        @functools.wraps(fn)
        def call(*args, **kwargs):
            from .errors import LimitError
            token = _log.current_txn.set(_log.new_txn())
            t0 = time.perf_counter()
            attempt = 1  # 2 once the idempotent reconnect-and-retry fires
            dropped: dict = {}  # {"hop": <hop that dropped>} once the retry fires
            before = op_token = None
            in_body = False  # True once the driver body runs (after limits + approval)
            try:
                # the policy is the operator's (ADR-001 addendum 5): an ENCLOSING op
                # that changed it before calling this one is caught here, pre-I/O
                outer = _op_policy.get()
                if outer is not None:
                    snap, outer_drv, outer_op, outer_txn = outer
                    _refuse_policy_change(outer_drv, outer_op, snap, txn=outer_txn,
                                          before_calling=f"{self.node.path} {op}")
                before = _policy_snapshot(self.node)
                hal = before[5]  # the node's Hal, bound once by shal.load (5b)
                op_token = _op_policy.set((before, self, op, _log.current_txn.get()))
                if guard is not None:
                    try:
                        guard.check(self, *args, **kwargs)  # LimitError: pre-I/O reject
                    except LimitError:
                        if audited:  # the ATTEMPT is on the record (safety review)
                            _audit.info("%s %s rejected by limits",
                                        self.node.id or self.node.path, op,
                                        extra={"event": "audit",
                                               "id": self.node.id or "",
                                               "path": self.node.path, "op": op,
                                               "outcome": "rejected",
                                               "txn": _log.current_txn.get()})
                        raise
                # limits passed -> ask before moving (pre-I/O, unbypassable)
                gated_now = _effective_gated(hal)  # this op's Hal (5b)
                if gatable and side_effect in gated_now:
                    _approve_or_raise(self, op, side_effect, sig, args, kwargs,
                                      gated_now)
                in_body = True
                try:
                    result = fn(self, *args, **kwargs)
                except HopError as e:
                    if retry and e.delivered == "no" and self.bus is not None:
                        # reconnect once, retry once — the common case stays magic,
                        # but a handled anomaly is WARNED, never silent (rule 4).
                        # Nothing reached the device the first time, so the ONE
                        # approval above covers this send (no second ask), and the
                        # call keeps ONE outcome record, marked attempt=2 and
                        # carrying the dropped hop in the stable `hop` field — the
                        # same key and meaning as this WARNING line (#194)
                        attempt = 2
                        dropped = {"hop": e.hop}
                        self.log.warning("reconnect-and-retry after drop (1/1)",
                                         event="retry", op=op, attempt=2, hop=e.hop)
                        self.bus.close()
                        self.bus.ensure_ready()
                        result = fn(self, *args, **kwargs)
                    else:
                        raise  # delivery unknown / non-idempotent: the USER decides
                duration = round((time.perf_counter() - t0) * 1000, 1)
                self.log.debug("%s ok", op, event="call", op=op, duration_ms=duration)
                if audited:
                    _audit.info("%s %s ok", self.node.id or self.node.path, op,
                                extra={"event": "audit", "id": self.node.id or "",
                                       "path": self.node.path, "op": op,
                                       "outcome": "ok", "duration_ms": duration,
                                       "attempt": attempt, **dropped,
                                       "txn": _log.current_txn.get()})
                return result
            except HopError as e:
                # DEBUG breadcrumb so the failure exists in the log stream too;
                # the exception remains the report (no ERROR — raise-or-log)
                duration = round((time.perf_counter() - t0) * 1000, 1)
                self.log.debug("%s raising %s (delivered=%s)",
                               op, type(e).__name__, e.delivered,
                               event="raise", op=op, hop=e.hop,
                               delivered=e.delivered, duration_ms=duration)
                if audited:
                    _audit.info("%s %s failed (delivered=%s)",
                                self.node.id or self.node.path, op, e.delivered,
                                extra={"event": "audit", "id": self.node.id or "",
                                       "path": self.node.path, "op": op,
                                       "outcome": "error", "delivered": e.delivered,
                                       "duration_ms": duration, "attempt": attempt,
                                       **dropped, "txn": _log.current_txn.get()})
                raise
            except _ShalError as e:
                # the device said no (#198): a shal.Error from the driver BODY —
                # never the pre-I/O LimitError/ApprovalDenied above (already
                # audited), never a HopError (caught first: it is a subclass). ONE
                # outcome record, then the SAME error re-raises untouched. The
                # message carries the error text as the caller sees it: error
                # messages are secret-free by rule (context.md non-negotiables).
                if audited and in_body:
                    duration = round((time.perf_counter() - t0) * 1000, 1)
                    _audit.info("%s %s device-error: %s",
                                self.node.id or self.node.path, op, e,
                                extra={"event": "audit", "id": self.node.id or "",
                                       "path": self.node.path, "op": op,
                                       "outcome": "device-error",
                                       "duration_ms": duration, "attempt": attempt,
                                       **dropped, "txn": _log.current_txn.get()})
                raise
            finally:
                try:
                    if op_token is not None:  # ...and THIS op changing it is caught here
                        _op_policy.reset(op_token)
                        _refuse_policy_change(self, op, before, body_ran=in_body)
                finally:
                    _log.current_txn.reset(token)

        call.__shal_wrapped__ = True
        return call


def _refuse_policy_change(driver, op: str, snap: _PolicySnap, *, txn: str | None = None,
                          before_calling: str = "", body_ran: bool = False) -> None:
    """If the policy differs from ``snap``, ``driver``'s ``op`` changed it during its
    call: restore it, audit the attempt (outcome ``policy-changed``, on ``op``'s
    own txn) and raise ``shal.Error``. No-op when nothing changed (ADR-001
    addendum 5). ``before_calling`` names the nested op that was refused because
    of it (pre-I/O); the record and the message blame the op that changed it."""
    from .errors import Error
    changed = _policy_changed(snap)
    if not changed:
        return
    _restore_policy(snap)
    node = driver.node
    what = ", ".join(changed)
    where = (f" before calling {before_calling}, which was refused before any I/O"
             if before_calling else " during the call")
    ran = " The op itself ran." if body_ran and not before_calling else ""
    extra = {"before_calling": before_calling} if before_calling else {}
    _audit.info("%s %s changed the approval policy (%s)%s; restored",
                node.id or node.path, op, what, where,
                extra={"event": "audit", "id": node.id or "", "path": node.path,
                       "op": op, "outcome": "policy-changed", "changed": changed,
                       **extra,
                       "txn": txn if txn is not None else _log.current_txn.get()})
    raise Error(f"{node.path}  {op} changed the approval policy ({what}){where}. "
                f"It was restored.{ran} Only the operator sets the policy "
                f"(the gated set and the approver), never a driver.")


def _approve_or_raise(driver, op: str, side_effect: str, sig, args, kwargs,
                      gated: frozenset[str]) -> None:
    """Consult the active Approver for one gated call — an op whose side_effect is
    in its Hal's effective gated set (``gated``; see _effective_gated). ALWAYS
    audits the decision — a gated op is never a read, so it is audited whether
    or not it is @idempotent — and raises ApprovalDenied (pre-I/O, nothing sent)
    on refusal (issue #14). Called ONCE per call: an idempotent retry after a
    delivered="no" drop is covered by this same decision (#194)."""
    from .approval import ApprovalRequest, ConsoleApprover, get_approver
    from .errors import ApprovalDenied
    bound = sig.bind(driver, *args, **kwargs)
    bound.apply_defaults()
    params = {k: v for k, v in bound.arguments.items() if k != "self"}
    node = driver.node
    txn = _log.current_txn.get()
    approver = get_approver()
    allowed = bool(approver.approve(ApprovalRequest(
        op=op, path=node.path, id=node.id or "", side_effect=side_effect,
        params=params, txn=txn)))
    # every approval decision is on the record (deterministic/replayable)
    outcome = "approved" if allowed else "denied"
    _audit.info("%s %s %s by approval", node.id or node.path, op, outcome,
                extra={"event": "audit", "id": node.id or "",
                       "path": node.path, "op": op, "outcome": outcome,
                       "side_effect": side_effect, "txn": txn,
                       # the ACTIVE gated set that decided it, so a narrowing
                       # leaves a trace (ADR-001 addendum 5, D27)
                       "gated": sorted(gated)})
    if not allowed:
        no_one = isinstance(approver, ConsoleApprover) and not approver.has_person()
        raise ApprovalDenied(
            f"{node.path}  {op} denied by the approval policy "
            f"— nothing was sent to the device",
            path=node.path, op=op, side_effect=side_effect, params=params,
            reason="no-approver" if no_one else None)


def _is_capability(fn: Callable) -> bool:
    """A public method defined by the driver (not inherited framework plumbing)."""
    return getattr(fn, "__qualname__", "").split(".")[0] not in ("Driver", "Transport", "object")
