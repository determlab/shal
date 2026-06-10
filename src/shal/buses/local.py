"""shal,local — CommandTransport executing argv on the local machine.

The root case of the recursive model, and the test bed for every
argv-rendering bus (i2c-cli, spi-cli). No shell. Ever.
"""
from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence

from .. import registry
from ..driver import Driver
from ..errors import HopError, HopTimeout
from ..log import bus_logger, current_txn
from ..node import Node
from ..transport import CommandTransport, Completed, Transport


@registry.register
class LocalBus(Driver, Transport, CommandTransport):
    compatible = "shal,local"
    kind = None  # sits at root (or behind another CommandTransport)

    DEFAULT_TIMEOUT_S = 30.0

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self.log = bus_logger("local", node.path)

    def run(self, argv: Sequence[str], stdin: bytes = b"") -> Completed:
        with self.lock:
            self.ensure_ready()
            t0 = time.perf_counter()
            try:
                p = subprocess.run(  # argv vector, shell=False by construction
                    list(argv), input=stdin, capture_output=True,
                    timeout=self.DEFAULT_TIMEOUT_S,
                )
            except FileNotFoundError as e:
                raise HopError(f"executable not found: {argv[0]}",
                               path=self.host.path, hop="local",
                               txn=current_txn.get(), delivered="no") from e
            except subprocess.TimeoutExpired as e:
                raise HopTimeout(f"{argv[0]} exceeded {self.DEFAULT_TIMEOUT_S}s",
                                 path=self.host.path, hop="local",
                                 txn=current_txn.get(), delivered="unknown") from e
            self.log.debug("run %s (%d args) exit %d", argv[0], len(argv) - 1,
                           p.returncode, event="run", op=str(argv[0]),
                           duration_ms=round((time.perf_counter() - t0) * 1000, 1))
            return Completed(stdout=p.stdout, stderr=p.stderr, exit=p.returncode)
