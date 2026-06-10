"""shal.sim — first-class shipped mock transport (DESIGN V2: product, not scaffolding).

Run code against simulated buses with zero hardware. Device models are built
from the children's `compatible` at activation; tests reach them via `model_for`.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from ..driver import Driver
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn, redact
from ..node import Node
from ..transport import ByteTransport, Op, Read, Transport, Write

# -- device models -------------------------------------------------------------

SIM_MODELS: dict[str, type] = {}


def sim_model(compatible: str):
    def deco(cls):
        SIM_MODELS[compatible] = cls
        return cls
    return deco


@sim_model("nxp,pca9548")
class Pca9548Model:
    """Control-register model; counts selects for cache regression tests."""

    def __init__(self) -> None:
        self.control = 0
        self.select_count = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self.control = op.data[0] if op.data else 0
                self.select_count += 1
            elif isinstance(op, Read):
                out += bytes([self.control])[: op.n]
        return out


@sim_model("ti,tmp102")
class Tmp102Model:
    def __init__(self) -> None:
        self.temp_c = 25.0
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._pointer = op.data[0] if op.data else self._pointer
            elif isinstance(op, Read):
                if self._pointer == 0:  # temperature register, 12-bit, 0.0625 C/LSB
                    raw = int(self.temp_c / 0.0625) & 0xFFF
                    out += bytes([(raw >> 4) & 0xFF, (raw & 0xF) << 4])[: op.n]
                else:
                    out += b"\x00" * op.n
        return out


# -- the bus ---------------------------------------------------------------------

class SimI2cBus(Driver, Transport, ByteTransport):
    """A node that provides ByteTransport to its children — entirely in memory."""

    compatible = "shal,sim-i2c"
    kind = None  # may sit at root, or behind any CommandTransport later

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[int, Any] = {}
        self.fail_next: int = 0          # test hook: fail N next txns (delivered=no)
        self.fail_delivered_unknown = False  # test hook: ambiguous failure
        self.connect_count = 0
        self.log = bus_logger("sim_i2c", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, int) or not (0x03 <= addr <= 0x77):
            raise LoadError(f"sim-i2c: invalid 7-bit I2C address {addr!r} "
                            f"(grammar: 0x03-0x77)")

    def activate(self) -> None:
        self.connect_count += 1
        # whole subtree: devices behind muxes are physically on this wire too
        for node in self.host.walk():
            if node is self.host:
                continue
            comp = getattr(node, "spec", {}).get("driver")
            model = SIM_MODELS.get(comp)
            if model is not None and isinstance(node.address, int):
                self._models.setdefault(node.address, model())
        super().activate()
        self.log.debug("connect (%d device models)", len(self._models),
                       event="connect")

    def model_for(self, addr: int) -> Any:
        self.ensure_ready()
        return self._models[addr]

    def txn(self, addr: int, ops: Sequence[Op]) -> bytes:
        with self.lock:  # check -> activate -> talk, under the bus lock
            self.ensure_ready()
            if self.fail_delivered_unknown:
                self.fail_delivered_unknown = False
                self._active = False
                raise HopError("connection lost after send", path=self.host.path,
                               hop="sim-i2c", txn=current_txn.get(), delivered="unknown")
            if self.fail_next > 0:
                self.fail_next -= 1
                self._active = False
                raise HopError("simulated link drop before send", path=self.host.path,
                               hop="sim-i2c", txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                raise HopError(f"i2c NAK at 0x{addr:02x}", path=self.host.path,
                               hop="sim-i2c", txn=current_txn.get())
            result = model.txn(ops)
            if self.log.isEnabledFor(logging.DEBUG):  # hot path costs nothing when off
                self.log.debug("txn -> %s", redact(result),
                               event="txn", addr=hex(addr))
            return result


from .. import registry  # noqa: E402

registry.register(SimI2cBus)
