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
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import log as _log
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


_SIDE_EFFECTS = frozenset({"none", "write", "actuator"})


def op(description: str, *, unit: str | None = None,
       side_effect: str | None = None,
       params: dict[str, dict] | None = None) -> Callable:
    """Attach LLM-facing metadata to a capability op (DESIGN V2 'agent bus').

    `description` should say WHEN to call it, not just what it does — that is what
    a model keys on. `side_effect` is "none" (a read), "write", or "actuator"
    (physical motion); if omitted it is inferred from @idempotent. The metadata
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
        # audit covers state-changing ops on DEVICE drivers; a bus's public
        # helpers are not device commands (and reads are not audited either)
        audited = not retry and not isinstance(self, Transport)
        # operating limits: effective schema (class ⊕ op_limits() ⊕ config.limits)
        # compiled ONCE at bind, checked on every call BEFORE the op body — the
        # only path to bus I/O (issue #10)
        schema, constrained = _limits.effective_schema(self, fn, op)
        self._op_schemas[op] = schema
        guard = (_limits.Guard(fn, schema, path=self.node.path, opname=op)
                 if constrained else None)
        # human-in-the-loop gate (issue #14): only physical-motion ops, and only
        # device drivers (a bus provides transport, not actuation — same rule as
        # audit). The approver is consulted at CALL time, so a host can inject a
        # policy after load. side_effect: explicit @op wins, else inferred.
        meta = getattr(fn, "__shal_op__", None) or {}
        side_effect = meta.get("side_effect") or ("none" if retry else "write")
        gated = side_effect == "actuator" and not isinstance(self, Transport)
        sig = inspect.signature(fn) if gated else None

        @functools.wraps(fn)
        def call(*args, **kwargs):
            from .errors import LimitError
            token = _log.current_txn.set(_log.new_txn())
            t0 = time.perf_counter()
            try:
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
                if gated:  # limits passed -> ask before moving (pre-I/O, unbypassable)
                    _approve_or_raise(self, op, side_effect, sig, args, kwargs, audited)
                try:
                    result = fn(self, *args, **kwargs)
                except HopError as e:
                    if retry and e.delivered == "no" and self.bus is not None:
                        # reconnect once, retry once — the common case stays magic,
                        # but a handled anomaly is WARNED, never silent (rule 4)
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
                                       "duration_ms": duration,
                                       "txn": _log.current_txn.get()})
                raise
            finally:
                _log.current_txn.reset(token)

        call.__shal_wrapped__ = True
        return call


def _approve_or_raise(driver, op: str, side_effect: str, sig, args, kwargs,
                      audited: bool) -> None:
    """Consult the active Approver for one actuator call. Audits the decision and
    raises ApprovalDenied (pre-I/O, nothing sent) on refusal (issue #14)."""
    from .approval import ApprovalRequest, get_approver
    from .errors import ApprovalDenied
    bound = sig.bind(driver, *args, **kwargs)
    bound.apply_defaults()
    params = {k: v for k, v in bound.arguments.items() if k != "self"}
    node = driver.node
    txn = _log.current_txn.get()
    allowed = bool(get_approver().approve(ApprovalRequest(
        op=op, path=node.path, id=node.id or "", side_effect=side_effect,
        params=params, txn=txn)))
    if audited:  # every approval decision is on the record (deterministic/replayable)
        outcome = "approved" if allowed else "denied"
        _audit.info("%s %s %s by approval", node.id or node.path, op, outcome,
                    extra={"event": "audit", "id": node.id or "",
                           "path": node.path, "op": op, "outcome": outcome,
                           "side_effect": side_effect, "txn": txn})
    if not allowed:
        raise ApprovalDenied(
            f"{node.path}  {op} denied by the approval policy "
            f"— nothing was sent to the device",
            path=node.path, op=op, side_effect=side_effect, params=params)


def _is_capability(fn: Callable) -> bool:
    """A public method defined by the driver (not inherited framework plumbing)."""
    return getattr(fn, "__qualname__", "").split(".")[0] not in ("Driver", "Transport", "object")
