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
Formatters that render these live in shal.logging (opt-in, app-side).
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

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
    return value.rsplit("@", 1)[-1]  # bare host:port — drop any userinfo prefix


# #457/#460: the text rule and the key-based secret rule, shared by the bus
# exchange hook (#457) and the CLI call log (#460) -- built once here so
# both callers redact the same way, rather than drifting apart the way the
# per-bus redaction calls #457 first shipped with did (CTO review).
_SECRET_KEY_RE = re.compile(r"token|password|passwd|secret|api_key|apikey|auth",
                            re.IGNORECASE)


def redact_secret_args(argv: Sequence[str]) -> list[str]:
    """A CLI argv with any ``--flag value`` or ``--flag=value`` whose flag
    name contains a secret keyword (token/password/passwd/secret/api_key/
    apikey/auth, case-insensitive) replaced with ``***``. Normal flags and
    values are returned unchanged (#460)."""
    out = list(argv)
    i = 0
    while i < len(out):
        arg = out[i]
        if arg.startswith("-"):
            flag, sep, _value = arg.partition("=")
            if _SECRET_KEY_RE.search(flag):
                if sep:
                    out[i] = f"{flag}=***"
                elif i + 1 < len(out):
                    out[i + 1] = "***"
        i += 1
    return out


#: A `scheme://...` substring, greedy to the next whitespace -- deliberately
#: narrower than "contains a URL": `redact_url` itself was written for a
#: value that IS an address end to end (a bare `host:port` counts too), not
#: for free text that happens to contain an `@` for an unrelated reason
#: (round 2: a real SCPI channel list, `MEAS:VOLT? (@1)`, has no `://`
#: anywhere and must never be touched).
#:
#: round 3 security fix: an EARLIER version of this pattern stopped the
#: match before trailing punctuation (`"')]}>,;`) directly, to keep a URL's
#: own wrapping intact. That was wrong: RFC 3986 allows `' ) , ;` unescaped
#: in a URL's userinfo as sub-delimiters, so stopping there could cut the
#: match INSIDE the userinfo -- `ftp://admin:1234,5678@host` matched only
#: `ftp://admin:1234`, which `redact_url` parses as a bare `host:port` with
#: no `@` left to redact, logging the password in full. The trailing-
#: punctuation strip has to happen AFTER the match, not as part of it --
#: see `redact_url_in_text`.
#:
#: round 5 security fix: plain greedy `\S+` has its own leak when a SECOND
#: url follows the first with no whitespace between them (a normal SCPI
#: comma-list reply: `http://a:b@h1,http://c:d@h2`) -- the first match
#: swallows the second URL whole, and `redact_url` on that combined string
#: keeps everything past the first host as path/query, so the second
#: URL's userinfo rides through untouched. The negative lookahead below
#: stops the match the instant a NEW `scheme://` begins, wherever that
#: falls (even mid-token, e.g. `b=http://`), so every URL in the string
#: gets its own match and its own redaction.
_URL_SUBSTRING_RE = re.compile(
    r"[A-Za-z][\w+.-]*://(?:(?![A-Za-z][\w+.-]*://)\S)+")

#: A trailing run of closing/punctuation characters a URL is commonly
#: wrapped or followed by in free text -- peeled off the END of a matched
#: substring (never from inside it) so "(http://u:p@h/x?token=a)" keeps its
#: own ")" instead of losing it to `redact_url`'s query-drop.
_TRAILING_PUNCT_RE = re.compile(r"""["')\]}>,;.]+$""")


def redact_url_in_text(value: str) -> str:
    """Cleans only the URL substrings a string of free text contains,
    leaving everything else untouched (#457/#460 round 2 security fix).
    `redact_url` was written for a value that IS a URL/address, not for
    text that might merely contain one — applying it to a whole string
    mangled ordinary text with an unrelated `@` (`MEAS:VOLT? (@1)`, a real
    SCPI channel list) and, worse, could leave a URL's own userinfo in
    place depending on where in the string it fell. Finds each
    `scheme://...` substring, peels any trailing closing punctuation off
    the END of it (never from inside -- round 3 security fix, see
    `_URL_SUBSTRING_RE`), and runs the real `redact_url` on just the core
    that's left, re-appending the peeled tail unchanged.

    Total, never raising (CTO review round 3): a malformed core (an
    unterminated IPv6 literal, a stray space in the host) makes
    `redact_url`'s `urlsplit` raise `ValueError`. Turning on the exchange
    log must never change what a bus call returns, so a core that fails to
    parse is replaced with a placeholder naming only its scheme — never
    passed through raw, and never partially: the whole core becomes the
    placeholder, not just the piece `urlsplit` choked on, or a password
    could still ride along in whatever part parsed."""
    def _sub(m: re.Match) -> str:
        whole = m.group(0)
        tail_m = _TRAILING_PUNCT_RE.search(whole)
        core, tail = (whole, "") if tail_m is None else (
            whole[:tail_m.start()], whole[tail_m.start():])
        try:
            cleaned = redact_url(core)
        except ValueError:
            scheme = core.split("://", 1)[0]
            cleaned = f"{scheme}://<redacted>"
        return cleaned + tail
    return _URL_SUBSTRING_RE.sub(_sub, value)


def redact_structured(value: Any) -> Any:
    """Recursively applies the text rule (`redact_url_in_text`) to every
    string, and masks any dict value whose key contains a secret keyword
    (same list as `redact_secret_args`) with ``***`` -- for JSON-like data
    (dict/list/tuple/str/number/bool/None). Shared by the bus exchange hook
    (#457, a structured message's string values) and the CLI call log
    (#460, its `json`/`text`)."""
    if isinstance(value, str):
        return redact_url_in_text(value)
    if isinstance(value, dict):
        return {k: ("***" if _SECRET_KEY_RE.search(k) else redact_structured(v))
               for k, v in value.items()}
    if isinstance(value, list):
        return [redact_structured(v) for v in value]
    if isinstance(value, tuple):
        return tuple(redact_structured(v) for v in value)
    return value


@dataclass(frozen=True)
class Exchange:
    """One real bus exchange, for an opt-in observer (#457) — never invented:
    built from exactly what a bus sent and got back, in its own protocol's
    natural shape (SCPI text, a structured message, or bytes for a byte
    transport), already sanitized by `record_exchange` before this is built
    — raw bytes through `redact`, every string (loose or inside a
    structured message) through the shared text/secret rule
    (`redact_structured`), `address` through `redact_url`."""

    bus_family: str
    path: str
    address: str
    request: Any
    response: Any


def _clean_payload(value: Any) -> Any:
    """CTO review on #457: the policy belongs here, the one place every bus
    already calls, not copied into each bus (where 2 of 5 redacted and 3 did
    not). `bytes` (I2C et al.) through `redact`; a raw `Op` sequence (a byte
    transport's own request, unjoined -- joining is deferred to here, so a
    bus with no sink active never does it) joins its `Write` data first,
    then the same way; everything else through the shared structured text/
    secret rule."""
    from .transport import Op, Write
    if isinstance(value, bytes):
        return redact(value)
    if isinstance(value, Sequence) and all(isinstance(o, Op) for o in value):
        return redact(b"".join(o.data for o in value if isinstance(o, Write)))
    return redact_structured(value)


ExchangeSink = Callable[[Exchange], None]

#: Off by default (#457): a bus checks this itself via `record_exchange` and
#: costs nothing when no sink is set. The buses also run against real
#: instruments, where an always-on exchange log would be a standing
#: liability — this is never turned on except by something that opted in.
_exchange_sink: contextvars.ContextVar[ExchangeSink | None] = contextvars.ContextVar(
    "shal_exchange_sink", default=None)


@contextlib.contextmanager
def exchange_sink(sink: ExchangeSink):
    """Opt-in hook (#457): while this is active, every real bus exchange
    started in this context calls ``sink(exchange)`` right after it
    completes — never before, and never for a call that raised. Nested use
    replaces the sink for its own scope and restores the outer one on exit."""
    token = _exchange_sink.set(sink)
    try:
        yield
    finally:
        _exchange_sink.reset(token)


def record_exchange(bus_family: str, path: str, address: Any, request: Any,
                    response: Any) -> None:
    """A bus calls this right after a real exchange completes, with its own
    RAW request/response (bytes, text, or a structured message) — never
    pre-redacted by the caller. No-op unless `exchange_sink` is active,
    checked FIRST so a bus pays nothing for this hot path when it is off
    (CTO review on #457: redaction work must not run before that check).
    Sanitizing is done here, once, so every bus gets it for free and
    cannot forget it, the way 2 of 5 did when each bus redacted for
    itself: `address` through `redact_url` (it is `${ENV}`-resolved, same
    as every other log line these buses already clean), `request`/
    `response` through `_clean_payload`.

    issue #466: the sink is someone else's code, called from inside a real
    bus call -- a bug in it (or in an observer's own storage) must never
    fail or change that call, in particular a DELIVERED write (`scpi_raw`/
    `sim_msg` call this after the device has already acted): a raising
    sink must never turn a change that really happened into a reported
    failure. `sink(exchange)` is the only thing guarded -- the `Exchange`
    above it is built, and fully sanitized, before the `try`, so a
    redaction bug still fails loudly instead of handing the sink
    unredacted data. The bus's own result, or its own exception, is
    unaffected either way; `KeyboardInterrupt`/`SystemExit` are not
    `Exception` subclasses, so they already propagate with no special
    case. A broken sink is one WARNING naming it (never the exchange it
    saw): `exc_info` carries only what the sink's own exception carries;
    the exchange fields it was handed are already sanitized."""
    sink = _exchange_sink.get()
    if sink is None:
        return
    exchange = Exchange(bus_family=bus_family, path=path, address=redact_url(str(address)),
                        request=_clean_payload(request), response=_clean_payload(response))
    try:
        sink(exchange)
    except Exception:
        name = getattr(sink, "__qualname__", type(sink).__qualname__)
        logging.getLogger("shal.log").warning(
            "exchange_sink %s raised; the bus call it observed is unaffected",
            name, exc_info=True)


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
