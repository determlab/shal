"""Logging discipline (DESIGN V2 'Logging & observability').

SHAL is a library: structured records, never configuration. Exactly one
NullHandler on the root 'shal' logger. Raise or log — never both (the rule
binds ERROR; DEBUG breadcrumbs before a raise are hop traces, not reports).

Record schema (rule 5) — stable `extra` fields, uniform across the tree:
    event       stable machine key (connect/txn/run/exchange/select/retry/
                raise/call/bind/env/ref/teardown/audit) — message TEXT is for
                humans and free to evolve; `event` is for machines and is not.
    path, hop, bus_family, addr, txn, attempt, op, duration_ms, delivered, via
    `via` (#236) names the route of a node with `routes:`; it is ABSENT on a
    node without routes, so those records are unchanged.
    device, address, simulated (#347) — on every call result and audit line
    (not the DriverLogAdapter's `id`/`path`, which are bound once at logger
    creation). `audit`'s own `id` and `path` and `call_identity`'s `device`
    (`node.id or node.path`) are deliberately three copies of one fact, not
    an oversight: `id`/`path` are the audit record's own long-standing shape,
    `device` is the #347 identity triple's own name, used the same way across
    every caller of `call_identity` regardless of whether that caller already
    had `id`/`path` to hand.
Formatters that render these live in shal.logging (opt-in, app-side).
"""
from __future__ import annotations

import contextvars
import logging
import re
import uuid

logging.getLogger("shal").addHandler(logging.NullHandler())

_audit = logging.getLogger("shal.audit")
_audit.addHandler(logging.NullHandler())
_audit.propagate = False  # silent by default; enable by attaching a handler

# txn correlation: one short id per user-level capability call (rule 6)
current_txn: contextvars.ContextVar[str] = contextvars.ContextVar("shal_txn", default="----")

# the route a routed node's transport call is on now (#236): set by the RouteSet
# around each route, so every hop line of that call carries `via`; None elsewhere
current_via: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "shal_via", default=None)


def new_txn() -> str:
    return uuid.uuid4().hex[:4]


def redact(payload: bytes, limit: int = 64) -> str:
    """Payload bytes only at DEBUG, hex-encoded, truncated (rule 7)."""
    h = payload[:limit].hex()
    return h + ("…" if len(payload) > limit else "")


def redact_url(value: str) -> str:
    """Strip credentials before a URL/endpoint reaches a log or error (rule 7).

    Removes any userinfo (``user:pass@``) and URL query/fragment, keeping
    ``scheme://host[:port]/path`` or a bare ``host:port``. A network endpoint is
    operational context worth keeping; a credential never is. This is the single
    sanitizer every bus routes addresses through (issue #20)."""
    import urllib.parse
    if "://" in value:
        p = urllib.parse.urlsplit(value)
        netloc = p.hostname or ""
        if p.port is not None:
            netloc = f"{netloc}:{p.port}"
        return urllib.parse.urlunsplit((p.scheme, netloc, p.path, "", ""))
    # bare host:port — drop any userinfo prefix, and any query/fragment (#347
    # round 2: a scheme-less address can still carry a secret as a query
    # param, e.g. "h:5025?token=s" — the userinfo strip alone left it in)
    bare = value.rsplit("@", 1)[-1]
    return re.split(r"[?#]", bare, maxsplit=1)[0]


def redact_address(addr):
    """A node's configured address, shown safely (#347 nit): a string address
    is `${ENV}`-resolved and may carry userinfo or a query token, so it goes
    through `redact_url`; anything else (an int bus address) is reported
    as-is. The one place `call_identity` and `declared_routes` share this
    rule, instead of each repeating the `isinstance` check."""
    return redact_url(addr) if isinstance(addr, str) else addr


_RESERVED_KWARGS = frozenset({"exc_info", "stack_info", "stacklevel", "extra"})


class _ShalLogAdapter(logging.LoggerAdapter):
    """Folds bound context + per-call structured fields into `extra`, and
    injects the current txn. Call sites pass fields as plain keyword args:

        self.log.debug("connect ok", event="connect", duration_ms=12)
    """

    def process(self, msg, kwargs):
        extra = kwargs.pop("extra", None) or {}
        fields = {k: kwargs.pop(k) for k in list(kwargs)
                  if k not in _RESERVED_KWARGS}
        merged = {**self.extra, **extra, **fields}
        merged["txn"] = current_txn.get()
        via = current_via.get()
        if via is not None and "via" not in merged:
            merged["via"] = via
        kwargs["extra"] = merged
        return msg, kwargs


class DriverLogAdapter(_ShalLogAdapter):
    """`self.log` for drivers — path/id/txn injected, fields come free (rule 10)."""


class BusLogAdapter(_ShalLogAdapter):
    """`self.log` for buses — path/bus_family/txn injected (rules 5 & 9)."""


def driver_logger(compatible: str, path: str, node_id: str | None) -> DriverLogAdapter:
    name = "shal.driver." + compatible.replace(",", ".")
    return DriverLogAdapter(logging.getLogger(name), {"path": path, "id": node_id or ""})


def bus_logger(family: str, path: str) -> BusLogAdapter:
    """Pre-bound logger for a bus instance: shal.bus.<family> with uniform fields."""
    return BusLogAdapter(logging.getLogger("shal.bus." + family),
                         {"path": path, "bus_family": family, "hop": family})
