"""shal,http — MessageTransport over HTTP(S). TLS required by default;
`insecure: true` is the loud per-node opt-out (DESIGN V2 'Security').

exchange(addr, msg): POST JSON to <base>/<addr>, JSON response back.
Stateless per request — is_active is trivially optimistic.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import Any

from .. import registry
from ..driver import Driver
from ..errors import HopError, HopTimeout, LoadError
from ..log import bus_logger, current_txn, redact_url
from ..node import Node
from ..transport import MessageTransport, Transport

DEFAULT_TIMEOUT_S = 10.0


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
        self.log = bus_logger("http", node.path)

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        url = f"{self.base}/{str(addr).lstrip('/')}"
        body = json.dumps(dict(msg)).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        with self.lock:
            self.ensure_ready()
            t0 = time.perf_counter()
            try:
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
                    out = json.loads(resp.read())
                # URL path only — query strings can carry credentials (rule 7)
                self.log.debug("POST %s -> %d", str(addr), resp.status,
                               event="exchange", addr=str(addr), status=resp.status,
                               duration_ms=round((time.perf_counter() - t0) * 1000, 1))
                return out
            except urllib.error.HTTPError as e:
                # server answered -> it was delivered; surface status, never retry
                # magically. redact_url strips any userinfo/query creds (issue #20)
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
