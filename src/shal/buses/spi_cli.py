"""shal,spi-cli — SPI rendered as spi-pipe argv over the parent CommandTransport.

SPI is full-duplex and unaddressed (chip select = device file). The Op sequence
maps onto one transfer: Writes send their bytes, Reads clock out zeros and
capture the same positions of the rx stream.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from .. import registry
from ..driver import Driver
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn
from ..node import Node
from ..transport import ByteTransport, CommandTransport, Op, Read, Transport, Write

_DEV_RE = re.compile(r"^/dev/spidev\d+\.\d+$")


@registry.register
class SpiCliBus(Driver, Transport, ByteTransport):
    compatible = "shal,spi-cli"
    kind = CommandTransport

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        if _DEV_RE.match(str(node.address)) is None:
            raise LoadError(f"{node.path}: spi-cli address must be /dev/spidevX.Y, "
                            f"got {node.address!r}")
        self.dev = str(node.address)
        self.log = bus_logger("spi_cli", node.path)

    def validate_address(self, addr: Any) -> None:
        pass  # chip select is the device file; child address is unused

    def txn(self, addr: Any, ops: Sequence[Op]) -> bytes:
        tx = bytearray()
        spans: list[tuple[int, int]] = []  # (offset, len) of each Read
        for op in ops:
            if isinstance(op, Write):
                tx += op.data
            elif isinstance(op, Read):
                spans.append((len(tx), op.n))
                tx += b"\x00" * op.n
        with self.lock:
            self.ensure_ready()
            argv = ["spi-pipe", "-d", self.dev, "-b", str(len(tx)), "-n", "1"]
            out = self.upstream.run(argv, stdin=bytes(tx))
            if out.exit != 0:
                raise HopError(
                    f"spi failure: {out.stderr.decode(errors='replace').strip()[:200]}",
                    path=self.host.path, hop="spi-cli",
                    txn=current_txn.get(), delivered="no")
            rx = out.stdout
            if len(rx) < len(tx):
                raise HopError(f"spi short read: {len(rx)}/{len(tx)} bytes",
                               path=self.host.path, hop="spi-cli",
                               txn=current_txn.get(), delivered="unknown")
            self.log.debug("txn %dB full-duplex", len(tx), event="txn")
            return b"".join(rx[off:off + n] for off, n in spans)
