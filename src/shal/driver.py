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
       side_effect: str | None = None) -> Callable:
    """Attach LLM-facing metadata to a capability op (DESIGN V2 'agent bus').

    `description` should say WHEN to call it, not just what it does — that is what
    a model keys on. `side_effect` is "none" (a read), "write", or "actuator"
    (physical motion); if omitted it is inferred from @idempotent. The metadata
    feeds `hal.tool_schemas()` and is required on every public op of a driver that
    sets `llm_ready = True` (checked at bind — fail loudly, never at call time).
    """
    if side_effect is not None and side_effect not in _SIDE_EFFECTS:
        raise ValueError(f"side_effect must be one of {sorted(_SIDE_EFFECTS)}")

    def deco(fn: Callable) -> Callable:
        fn.__shal_op__ = {"description": description, "unit": unit,
                          "side_effect": side_effect}
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

    def provide_child_bus(self, child: Node) -> Transport | None:
        """A driver that exposes a distinct bus per child (mux channels)
        returns it here; None means 'use this driver if it is a Transport'."""
        return None

    # -- bind-time wrapping: txn id on every capability call; retry iff idempotent
    _PLUMBING = frozenset({
        "bind", "safe_state", "kinds", "provide_child_bus", # Driver/Transport API
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
        ops = type(self).capability_ops()
        if self.llm_ready:  # opt-in conformance: every op must carry @shal.op
            missing = [n for n, fn in ops.items()
                       if not getattr(fn, "__shal_op__", None)
                       or not fn.__shal_op__.get("description")]
            if missing:
                raise _LoadError(
                    f"{self.compatible}: llm_ready driver is missing @shal.op "
                    f"metadata on: {', '.join(sorted(missing))}")
        for name, fn in ops.items():
            if not getattr(getattr(type(self), name), "__shal_wrapped__", False):
                setattr(self, name, self._make_call(fn))

    def _make_call(self, fn: Callable) -> Callable:
        from .transport import Transport  # local: avoid import cycle at module load
        retry = getattr(fn, "__shal_idempotent__", False)
        op = fn.__name__
        # audit covers state-changing ops on DEVICE drivers; a bus's public
        # helpers are not device commands (and reads are not audited either)
        audited = not retry and not isinstance(self, Transport)

        @functools.wraps(fn)
        def call(*args, **kwargs):
            token = _log.current_txn.set(_log.new_txn())
            t0 = time.perf_counter()
            try:
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


def _is_capability(fn: Callable) -> bool:
    """A public method defined by the driver (not inherited framework plumbing)."""
    return getattr(fn, "__qualname__", "").split(".")[0] not in ("Driver", "Transport", "object")
