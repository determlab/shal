"""shal,http — MessageTransport over HTTP(S). TLS required by default;
`insecure: true` is the loud per-node opt-out (DESIGN V2 'Security').

exchange(addr, msg) takes one of two message shapes, chosen by the message
(issue #104; ADK spec §3.7 S1):

- **plain** (the default) — no reserved key present: the whole mapping is
  POSTed as JSON to <base>/<addr>, and the reply is the parsed JSON body.
- **request envelope** — a mapping carrying any of the reserved keys
  ``method`` / ``path`` / ``query`` / ``headers`` / ``json`` (``_ENVELOPE_KEYS``)
  is rendered as that one HTTP request to <base>/<addr>/<path>?<query>. The
  reply is ``{"status": int, "headers": {...}, "json": ...}`` for a JSON body,
  else ``{"status": int, "headers": {...}, "text": "..."}``. ``None``-valued
  query params are dropped; a ``json`` body on GET/HEAD is a LoadError.

Invariants:
- Credentials live on the BUS node: ``config: {headers: {...}}``, ``${ENV}``
  references resolved at bind. The bus adds them to every request, they win
  over an envelope header of the same name, they are never forwarded across a
  redirect, and they never reach a log, an error, or the reply (the reply's
  ``headers`` are the RESPONSE headers). A driver never holds a secret.
- A non-2xx answer is a HopError naming the status — never a retry.
- Logs carry ``<addr>/<path>`` and the status only: never the query, never a
  header (rule 7). Error text routes the URL through ``redact_url``.
Stateless per request — is_active is trivially optimistic.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from .. import registry
from ..driver import Driver
from ..errors import HopError, HopTimeout, LoadError
from ..loader import _resolve_env
from ..log import bus_logger, current_txn, redact_url
from ..node import Node
from ..transport import MessageTransport, Transport

DEFAULT_TIMEOUT_S = 10.0

# Reserved: a message carrying ANY of these is a request envelope, not a body.
_ENVELOPE_KEYS = frozenset({"method", "path", "query", "headers", "json"})
_BODYLESS = frozenset({"GET", "HEAD"})
_ENV_REF = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\}")


def is_envelope(msg: Mapping) -> bool:
    """True when ``msg`` is a request envelope (carries a reserved key)."""
    return any(k in msg for k in _ENVELOPE_KEYS)


def parse_envelope(msg: Mapping, where: str) -> dict:
    """Validate and normalise a request envelope. Shared by ``shal,sim-msg`` so
    the twin sees exactly what the wire would. Errors name keys, never values."""
    extra = sorted(str(k) for k in msg if k not in _ENVELOPE_KEYS)
    if extra:
        raise LoadError(f"{where}: a request envelope takes only "
                        f"{sorted(_ENVELOPE_KEYS)}; unexpected key(s) {extra} "
                        f"— put a request body under `json`")
    body = msg.get("json")
    method = msg.get("method") or ("GET" if body is None else "POST")
    if not isinstance(method, str) or not method.isalpha():
        raise LoadError(f"{where}: envelope `method` must be an HTTP method "
                        f"name like 'GET'")
    method = method.upper()
    if body is not None and method in _BODYLESS:
        raise LoadError(f"{where}: a `json` body on {method} is not portable "
                        f"— send it as `query`, or use POST")
    path = msg.get("path") or ""
    if not isinstance(path, (str, int)):
        raise LoadError(f"{where}: envelope `path` must be a string")
    query = msg.get("query") or {}
    headers = msg.get("headers") or {}
    for key, value in (("query", query), ("headers", headers)):
        if not isinstance(value, Mapping):
            raise LoadError(f"{where}: envelope `{key}` must be a mapping")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise LoadError(f"{where}: envelope `headers` must map names to strings")
    return {
        "method": method,
        "path": str(path).lstrip("/"),
        # None drops the param, so a driver passes optional args without branching
        "query": {k: _query_value(v) for k, v in query.items() if v is not None},
        "headers": dict(headers),
        "json": body,
    }


def _query_value(v: Any) -> Any:
    if isinstance(v, bool):  # HTTP APIs spell booleans lowercase
        return "true" if v else "false"
    if isinstance(v, (list, tuple)):
        return [_query_value(x) for x in v]
    return v


def _configured_headers(node: Node) -> dict[str, str]:
    """The bus node's ``config.headers``, ``${ENV}`` references resolved (also
    inside a value: ``"Bearer ${TOKEN}"``). A missing variable is a LoadError
    naming the variable, never a value (the loader's own resolver)."""
    cfg = getattr(node, "spec", {}).get("config") or {}
    raw = cfg.get("headers") or {}
    if not isinstance(raw, Mapping) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in raw.items()):
        raise LoadError(f"{node.path}: config.headers must map header names "
                        f"to strings")
    return {k: _ENV_REF.sub(lambda m: _resolve_env(m.group(0)), v)
            for k, v in raw.items()}


@registry.register
class HttpBus(Driver, Transport, MessageTransport):
    compatible = "shal,http"
    kind = None

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self.base = str(node.address).rstrip("/")
        scheme = urllib.parse.urlsplit(self.base).scheme
        insecure = bool(getattr(node, "spec", {}).get("insecure", False))
        if scheme == "http" and not insecure:
            raise LoadError(f"{node.path}: plaintext http requires `insecure: true` "
                            f"(TLS is the default, not an option)")
        if scheme not in ("http", "https"):
            # redact_url: a ${ENV} address may carry userinfo creds (issue #101)
            raise LoadError(f"{node.path}: http bus address must be an "
                            f"http(s):// URL, got {redact_url(str(node.address))!r}")
        self._headers = _configured_headers(node)  # private: never handed out
        self.log = bus_logger("http", node.path)

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        url = f"{self.base}/{str(addr).lstrip('/')}"
        target = str(addr)
        envelope = parse_envelope(msg, self.host.path) if is_envelope(msg) else None
        if envelope is None:  # plain: the mapping IS the body
            method = "POST"
            body: bytes | None = json.dumps(dict(msg)).encode("utf-8")
            headers = {"Content-Type": "application/json"}
        else:
            method = envelope["method"]
            if envelope["path"]:
                url = f"{url.rstrip('/')}/{envelope['path']}"
                target = f"{target}/{envelope['path']}"
            if envelope["query"]:
                url += "?" + urllib.parse.urlencode(envelope["query"], doseq=True)
            configured = {k.lower() for k in self._headers}
            headers = {k: v for k, v in envelope["headers"].items()
                       if k.lower() not in configured}  # the bus's headers win
            body = None
            if envelope["json"] is not None:
                body = json.dumps(envelope["json"]).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        for k, v in self._headers.items():
            req.add_unredirected_header(k, v)  # never forwarded to a 3xx target
        with self.lock:
            self.ensure_ready()
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
                    raw = resp.read()
                    status = resp.status
                    out = (json.loads(raw) if envelope is None
                           else _envelope_reply(status, resp.headers, raw))
                # <addr>/<path> only — a query or a header can carry credentials (rule 7)
                self.log.debug("%s %s -> %d", method, target, status,
                               event="exchange", addr=target, status=status,
                               duration_ms=round((time.perf_counter() - t0) * 1000, 1))
                return out
            except urllib.error.HTTPError as e:
                # server answered -> it was delivered; surface status, never retry
                # magically. redact_url strips any userinfo/query creds (issue #20)
                self.log.debug("%s %s -> %d", method, target, e.code,
                               event="exchange", addr=target, status=e.code)
                raise HopError(f"HTTP {e.code} from {redact_url(url)}",
                               path=self.host.path, hop="http",
                               txn=current_txn.get(), delivered="unknown") from e
            except urllib.error.URLError as e:
                reason = getattr(e, "reason", e)
                delivered = "no" if isinstance(reason, ConnectionRefusedError) else "unknown"
                raise HopError(f"request failed: {reason}", path=self.host.path,
                               hop="http", txn=current_txn.get(),
                               delivered=delivered) from e
            except TimeoutError as e:
                raise HopTimeout("http request", path=self.host.path, hop="http",
                                 txn=current_txn.get(), delivered="unknown") from e


def _envelope_reply(status: int, headers: Any, raw: bytes) -> dict:
    """``{status, headers, json | text}`` — ``headers`` are the RESPONSE's."""
    out: dict[str, Any] = {"status": status, "headers": dict(headers.items())}
    ctype = headers.get_content_type() if hasattr(headers, "get_content_type") else ""
    if raw and (ctype == "application/json" or ctype.endswith("+json")):
        try:
            out["json"] = json.loads(raw)
            return out
        except ValueError:
            pass  # labelled JSON, isn't: hand the driver the text, not a crash
    out["text"] = raw.decode("utf-8", errors="replace")
    return out
