"""Logging discipline (DESIGN V2 'Logging & observability').

SHAL is a library: structured records, never configuration. Exactly one
NullHandler on the root 'shal' logger. Raise or log — never both (the rule
binds ERROR; DEBUG breadcrumbs before a raise are hop traces, not reports).

Record schema (rule 5) — stable `extra` fields, uniform across the tree:
    event       stable machine key (connect/txn/run/exchange/select/retry/
                raise/call/bind/env/ref/teardown/audit) — message TEXT is for
                humans and free to evolve; `event` is for machines and is not.
    path, hop, bus_family, addr, txn, attempt, op, duration_ms, delivered
Formatters that render these live in shal.logging (opt-in, app-side).
"""
from __future__ import annotations

import contextvars
import logging
import uuid

logging.getLogger("shal").addHandler(logging.NullHandler())

_audit = logging.getLogger("shal.audit")
_audit.addHandler(logging.NullHandler())
_audit.propagate = False  # silent by default; enable by attaching a handler

# txn correlation: one short id per user-level capability call (rule 6)
current_txn: contextvars.ContextVar[str] = contextvars.ContextVar("shal_txn", default="----")


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
    return value.rsplit("@", 1)[-1]  # bare host:port — drop any userinfo prefix


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
