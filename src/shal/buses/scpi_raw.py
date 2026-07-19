"""shal,scpi-raw — SCPI command/response over a raw TCP socket (the lab :5025
convention). No VISA needed; stdlib sockets only.

MessageTransport contract for this family: ``msg = {"scpi": "<command>", "query":
bool}``. A query writes the line, reads one reply line, returns ``{"reply":
"<text>"}``; a write returns ``{"reply": ""}`` (SCPI writes have no response).
Line-delimited (``\\n``), UTF-8. SCPI instruments do not speak TLS, so this bus is
plaintext and REQUIRES a loud ``insecure: true`` to acknowledge that at load.
"""
from __future__ import annotations

import socket
import time
from collections.abc import Mapping
from typing import Any

from .. import registry
from ..driver import Driver
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn, redact_url
from ..node import Node
from ..transport import MessageTransport, Transport

DEFAULT_TIMEOUT_S = 10.0


@registry.register
class ScpiRawBus(Driver, Transport, MessageTransport):
    compatible = "shal,scpi-raw"
    kind = None  # a leaf network transport (like tcp): opens its own socket

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        addr = str(node.address)
        host, sep, port = addr.rpartition(":")
        if not sep or not port.isdigit():
            # redact_url: a misplaced creds-URL address must not echo verbatim (issue #101)
            raise LoadError(f"{node.path}: scpi-raw address must be host:port, "
                            f"got {redact_url(addr)!r}")
        if not bool(getattr(node, "spec", {}).get("insecure", False)):
            raise LoadError(f"{node.path}: scpi-raw is plaintext (SCPI instruments "
                            f"don't do TLS); set `insecure: true` to acknowledge")
        self.scpi_host, self.scpi_port = host, int(port)
        self._sock: socket.socket | None = None
        self._file = None
        self.log = bus_logger("scpi_raw", node.path)

    def is_active(self) -> bool:
        return self._sock is not None

    def activate(self) -> None:
        t0 = time.perf_counter()
        try:
            raw = socket.create_connection((self.scpi_host, self.scpi_port),
                                           timeout=DEFAULT_TIMEOUT_S)
            self._sock = raw
            self._file = raw.makefile("rwb")
        except OSError as e:
            raise HopError(f"connect failed: {e}", path=self.host.path,
                           hop="scpi-raw", txn=current_txn.get(),
                           delivered="no") from e
        # log the endpoint, never any userinfo a ${ENV} address may carry (issue #20)
        self.log.info("connect %s", redact_url(f"{self.scpi_host}:{self.scpi_port}"),
                      event="connect",
                      duration_ms=round((time.perf_counter() - t0) * 1000, 1))

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._file.close()
                self._sock.close()
            except OSError:
                pass
            self.log.info("close", event="close")
        self._sock = None
        self._file = None

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, (str, int)) or str(addr) == "":
            raise LoadError(f"scpi-raw: child address must be a non-empty "
                            f"instrument/channel label, got {addr!r}")

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:
            self.ensure_ready()
            cmd = msg["scpi"]
            query = bool(msg.get("query"))
            try:
                self._file.write((cmd + "\n").encode("utf-8"))
                self._file.flush()
            except OSError as e:
                self.close()
                raise HopError(f"send failed: {e}", path=self.host.path,
                               hop="scpi-raw", txn=current_txn.get(),
                               delivered="no") from e
            if not query:                       # a write has no response
                self.log.debug("write %r", cmd, event="exchange", addr=str(addr))
                return {"reply": ""}
            try:
                reply = self._file.readline()
            except OSError as e:
                self.close()
                raise HopError(f"connection lost after send: {e}",
                               path=self.host.path, hop="scpi-raw",
                               txn=current_txn.get(), delivered="unknown") from e
            if not reply:
                self.close()
                raise HopError("connection closed after query", path=self.host.path,
                               hop="scpi-raw", txn=current_txn.get(),
                               delivered="unknown")
            self.log.debug("query %r", cmd, event="exchange", addr=str(addr))
            return {"reply": reply.decode("utf-8", errors="replace").strip()}
