"""shal,remote-bus -- a bus whose wire is a ``shal-arena serve`` server (issue #486).

The agent's driver.py runs on the client; its ``self.bus.exchange(...)`` (SCPI
and Modbus messages, ``MessageTransport``) and ``self.bus.txn(...)`` (I2C
bytes, ``ByteTransport``) go over the wire as JSON to the server's sim bus,
which answers like a networked instrument. The seed, the fault and the card
model never leave the server.

The node's address is the server URL; ``config`` carries the per-run token and
the session one ``measure``/``call`` opened (``run_id``, ``token``,
``session``). Every request carries them. I2C bytes are hex strings. A
request the server refuses (403 wrong token, 413 over the payload cap) is a
``HopError`` with ``delivered="no"`` that names the server's own fix; a
failure of the simulated bus itself comes back with the delivery state the
sim reported.

Registered when this module is imported (``client.py`` does that), not by
entry point: the server never needs it.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from typing import Any

from shal import registry
from shal.driver import Driver
from shal.errors import HopError, HopTimeout, LoadError
from shal.log import bus_logger, current_txn, redact_url
from shal.node import Node
from shal.transport import ByteTransport, MessageTransport, Read, Transport, Write

DEFAULT_TIMEOUT_S = 10.0


@registry.register
class RemoteBus(Driver, Transport, MessageTransport, ByteTransport):
    compatible = "shal,remote-bus"
    kind = None
    simulated = True  # the server's bus is a SHAL simulator

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self.base = str(node.address).rstrip("/")
        if urllib.parse.urlsplit(self.base).scheme not in ("http", "https"):
            raise LoadError(f"{node.path}: remote-bus address must be the http(s):// URL "
                            f"of a shal-arena server, got {redact_url(str(node.address))!r}")
        cfg = getattr(node, "spec", {}).get("config") or {}
        self._creds = {k: cfg.get(k) for k in ("run_id", "token", "session")}
        if not all(isinstance(v, str) and v for v in self._creds.values()):
            raise LoadError(f"{node.path}: remote-bus config needs run_id, token and "
                            "session (strings)")
        self.log = bus_logger("remote_bus", node.path)

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        doc = self._post("/api/remote/exchange", {"address": addr, "message": dict(msg)})
        return doc["reply"]

    def txn(self, addr: Any, ops: Sequence[Any]) -> bytes:
        wire = [{"op": "write", "hex": o.data.hex()} if isinstance(o, Write)
                else {"op": "read", "n": o.n} for o in ops if isinstance(o, (Write, Read))]
        return bytes.fromhex(self._post("/api/remote/txn", {"address": addr, "ops": wire})["hex"])

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            data = json.dumps({**self._creds, **body}).encode("utf-8")
        except (TypeError, ValueError) as e:
            raise LoadError(f"{self.host.path}: the message is not JSON-serializable: {e}") from e
        req = urllib.request.Request(self.base + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with self.lock:
            self.ensure_ready()
            try:
                with urllib.request.urlopen(req, timeout=DEFAULT_TIMEOUT_S) as resp:
                    doc = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                raise HopError(_refusal(e), path=self.host.path, hop="remote-bus",
                               txn=current_txn.get(), delivered="no") from e
            except urllib.error.URLError as e:
                reason = getattr(e, "reason", e)
                delivered = "no" if isinstance(reason, ConnectionRefusedError) else "unknown"
                raise HopError(f"request failed: {reason}", path=self.host.path,
                               hop="remote-bus", txn=current_txn.get(),
                               delivered=delivered) from e
            except TimeoutError as e:
                raise HopTimeout("remote-bus request", path=self.host.path, hop="remote-bus",
                                 txn=current_txn.get(), delivered="unknown") from e
        if not doc.get("ok"):
            err = doc.get("bus_error") or {}
            raise HopError(str(err.get("message", "the remote bus failed")),
                           path=self.host.path, hop="remote-bus", txn=current_txn.get(),
                           delivered=err.get("delivered", "unknown"))
        return doc


def _refusal(e: urllib.error.HTTPError) -> str:
    """The server's own refusal text and fix, e.g. the 403 "pass the token that
    `run` returned" or the 413 that names the payload cap."""
    try:
        err = json.loads(e.read())["error"]
        return f"HTTP {e.code}: {err['message']} (fix: {err['fix']})"
    except (ValueError, KeyError, TypeError):
        return f"HTTP {e.code} from the shal-arena server"
