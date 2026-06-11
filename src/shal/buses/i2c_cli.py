"""shal,i2c-cli — I2C rendered as i2ctransfer argv, carried by the parent
CommandTransport (the canonical DESIGN V2 example). Far side needs only i2c-tools.
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


def op_summary(ops: Sequence[Op]) -> str:
    """'w1 r2' — human-readable shape of a transaction, payload-free."""
    return " ".join(f"w{len(o.data)}" if isinstance(o, Write) else f"r{o.n}"
                    for o in ops)

_DEV_RE = re.compile(r"^/dev/i2c-(\d+)$")


def render_ops(addr: int, ops: Sequence[Op]) -> list[str]:
    """Pure renderer: ops -> i2ctransfer message arguments. Unit-testable."""
    parts: list[str] = []
    first = True
    for op in ops:
        at = f"@0x{addr:02x}" if first else ""
        if isinstance(op, Write):
            parts.append(f"w{len(op.data)}{at}")
            parts.extend(f"0x{b:02x}" for b in op.data)
        elif isinstance(op, Read):
            parts.append(f"r{op.n}{at}")
        first = False
    return parts


def parse_output(stdout: bytes) -> bytes:
    """i2ctransfer prints read bytes as hex tokens ('0x19 0x00')."""
    return bytes(int(tok, 16) for tok in stdout.split())


@registry.register
class I2cCliBus(Driver, Transport, ByteTransport):
    compatible = "shal,i2c-cli"
    kind = CommandTransport  # parent must carry argv

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        m = _DEV_RE.match(str(node.address))
        if m is None:
            raise LoadError(f"{node.path}: i2c-cli address must be /dev/i2c-<n>, "
                            f"got {node.address!r}")
        self.busnum = int(m.group(1))
        self.log = bus_logger("i2c_cli", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, int) or not (0x03 <= addr <= 0x77):
            raise LoadError(f"i2c-cli: invalid 7-bit I2C address {addr!r} "
                            f"(grammar: 0x03-0x77)")

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {"type": "string", "pattern": r"^/dev/i2c-\d+$",
                               "description": "host I2C device path",
                               "examples": ["/dev/i2c-1"]},
            "child_address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                                     "description": "7-bit I2C address", "examples": [72]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }

    def txn(self, addr: int, ops: Sequence[Op]) -> bytes:
        with self.lock:
            self.ensure_ready()
            argv = ["i2ctransfer", "-y", str(self.busnum), *render_ops(addr, ops)]
            out = self.upstream.run(argv)  # CommandTransport carries it
            if out.exit != 0:
                raise HopError(
                    f"i2c failure at 0x{addr:02x}: "
                    f"{out.stderr.decode(errors='replace').strip()[:200]}",
                    path=self.host.path, hop="i2c-cli",
                    txn=current_txn.get(), delivered="no")
            self.log.debug("txn %s", op_summary(ops), event="txn",
                           addr=f"0x{addr:02x}")
            return parse_output(out.stdout)
