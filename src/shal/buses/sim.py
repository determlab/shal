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


@sim_model("ti,ina219")
class Ina219Model:
    """Bus-voltage / current registers; mirrors the ina219 driver's decode."""

    def __init__(self) -> None:
        self.bus_v = 12.0
        self.current = 0.5
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._pointer = op.data[0] if op.data else self._pointer
            elif isinstance(op, Read):
                if self._pointer == 0x02:        # bus voltage, value in bits 15:3
                    raw = (int(round(self.bus_v / 0.004)) << 3) & 0xFFFF
                elif self._pointer == 0x04:       # current, 100 uA/LSB, signed
                    raw = int(round(self.current / 0.0001)) & 0xFFFF
                else:
                    raw = 0
                out += bytes([(raw >> 8) & 0xFF, raw & 0xFF])[: op.n]
        return out


@sim_model("microchip,mcp9808")
class Mcp9808Model:
    def __init__(self) -> None:
        self.temp_c = 22.5
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._pointer = op.data[0] if op.data else self._pointer
            elif isinstance(op, Read):
                if self._pointer == 0x05:          # 13-bit, 0.0625 C/LSB, sign bit12
                    val = int(round(self.temp_c * 16))
                    if val < 0:
                        val = 0x2000 + val
                    out += bytes([(val >> 8) & 0x1F, val & 0xFF])[: op.n]
                else:
                    out += b"\x00" * op.n
        return out


@sim_model("ti,ads1115")
class Ads1115Model:
    def __init__(self) -> None:
        self.voltages = {0: 1.0, 1: 2.0, 2: 0.5, 3: -1.0}
        self._pointer = 0
        self._channel = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                data = op.data
                self._pointer = data[0] if data else self._pointer
                if self._pointer == 0x01 and len(data) >= 3:   # config write
                    mux = (((data[1] << 8) | data[2]) >> 12) & 0x7
                    if mux >= 4:                                 # single-ended AIN
                        self._channel = mux - 4
            elif isinstance(op, Read):
                if self._pointer == 0x00:
                    val = int(round(self.voltages.get(self._channel, 0.0)
                                    * 32768 / 4.096)) & 0xFFFF
                    out += bytes([(val >> 8) & 0xFF, val & 0xFF])[: op.n]
                else:
                    out += b"\x00" * op.n
        return out


@sim_model("microchip,mcp23017")
class Mcp23017Model:
    def __init__(self) -> None:
        self.regs = {0x00: 0xFF, 0x01: 0xFF}   # IODIRA/B default all inputs
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                data = op.data
                if not data:
                    continue
                self._pointer = data[0]
                if len(data) >= 2:
                    self.regs[self._pointer] = data[1]
            elif isinstance(op, Read):
                reg = self._pointer
                if reg in (0x12, 0x13):            # GPIO reads loop back OLAT
                    reg += 2
                out += bytes([self.regs.get(reg, 0)])[: op.n]
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
