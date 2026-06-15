"""shal,tcp — MessageTransport over a TCP socket, TLS by default.

Framing (documented contract of this bus family): one JSON object per line,
UTF-8. Request envelope {"addr": <child address>, "payload": <msg>}; response
is any JSON object. Plaintext requires a loud per-node `insecure: true`.
"""
from __future__ import annotations

import json
import socket
import ssl
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
class TcpBus(Driver, Transport, MessageTransport):
    compatible = "shal,tcp"
    kind = None

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        addr = str(node.address)
        host, sep, port = addr.rpartition(":")
        if not sep or not port.isdigit():
            raise LoadError(f"{node.path}: tcp address must be host:port, got {addr!r}")
        self.tcp_host, self.tcp_port = host, int(port)
        self.insecure = bool(getattr(node, "spec", {}).get("insecure", False))
        self._sock: socket.socket | None = None
        self._file = None
        self.log = bus_logger("tcp", node.path)

    def is_active(self) -> bool:
        return self._sock is not None

    def activate(self) -> None:
        t0 = time.perf_counter()
        try:
            raw = socket.create_connection((self.tcp_host, self.tcp_port),
                                           timeout=DEFAULT_TIMEOUT_S)
            if not self.insecure:  # TLS by default — never the other way around
                ctx = ssl.create_default_context()
                raw = ctx.wrap_socket(raw, server_hostname=self.tcp_host)
            self._sock = raw
            self._file = raw.makefile("rwb")
        except OSError as e:
            raise HopError(f"connect failed: {e}", path=self.host.path, hop="tcp",
                           txn=current_txn.get(), delivered="no") from e
        # log the endpoint, never any userinfo a ${ENV} address may carry (issue #20)
        self.log.info("connect %s tls=%s", redact_url(f"{self.tcp_host}:{self.tcp_port}"),
                      not self.insecure, event="connect",
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

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:
            self.ensure_ready()
            t0 = time.perf_counter()
            line = json.dumps({"addr": addr, "payload": dict(msg)}) + "\n"
            try:
                self._file.write(line.encode("utf-8"))
                self._file.flush()
            except OSError as e:
                self.close()
                raise HopError(f"send failed: {e}", path=self.host.path, hop="tcp",
                               txn=current_txn.get(), delivered="no") from e
            try:
                reply = self._file.readline()
            except OSError as e:
                self.close()
                raise HopError(f"connection lost after send: {e}",
                               path=self.host.path, hop="tcp",
                               txn=current_txn.get(), delivered="unknown") from e
            if not reply:
                self.close()
                raise HopError("connection closed after send", path=self.host.path,
                               hop="tcp", txn=current_txn.get(), delivered="unknown")
            self.log.debug("exchange %dB out %dB in", len(line), len(reply),
                           event="exchange", addr=str(addr),
                           duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            return json.loads(reply)
