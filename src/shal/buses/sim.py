"""shal.sim — first-class shipped mock transport (DESIGN V2: product, not scaffolding).

Run code against simulated buses with zero hardware. Device models are built
from the children's `compatible` at activation; tests reach them via `model_for`.

It also ships the sim family's one device, `shal,sim-sensor` (R10, #156): a
temperature that drifts and one gated `config` op. It wraps no part, so it binds
only under a `shal,sim-i2c` bus — the framework's own object, not a `vendor,part`.
"""
from __future__ import annotations

import logging
import random
from collections.abc import Sequence
from typing import Any

from ..capabilities import TemperatureSensor
from ..driver import Driver, idempotent
from ..driver import op as _op  # `op` is the loop name in every model below
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn, redact, redact_url
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


# The sim family's own device (R10, #156): not a `vendor,part` twin but the
# device the twin machinery ships, so a bare install has something to read.
_SENSOR_TEMP = 0x00     # read-only: temperature, signed 16-bit, 0.01 C/LSB
_SENSOR_TARGET = 0x01   # write: the setpoint the value drifts toward, same encoding


def _centi(celsius: float) -> int:
    return int(round(celsius * 100))


@sim_model("shal,sim-sensor")
class SimSensorModel:
    """A room that drifts. Every read of the temperature register is a new
    conversion: the value moves toward `target_c` with noise, and always by at
    least `MIN_STEP`, so two reads differ. The live value IS this state (rule 3)
    — nothing is seeded into the driver or cached. The start value is random, so
    two processes (two `shal probe` runs) read different values too."""

    MIN_STEP = 0.05   # C; five LSBs, so every drift shows in the reading

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng or random.Random()
        self.target_c = 25.0
        self.temp_c = self.target_c + self._rng.uniform(-2.0, 2.0)
        self.target_writes = 0   # test hook: setpoint writes that reached the model
        self._pointer = _SENSOR_TEMP

    def _drift(self) -> None:
        step = 0.2 * (self.target_c - self.temp_c) + self._rng.uniform(-0.3, 0.3)
        if abs(step) < self.MIN_STEP:
            step = self.MIN_STEP if step >= 0 else -self.MIN_STEP
        self.temp_c += step

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for o in ops:
            if isinstance(o, Write):
                data = o.data
                if not data:
                    continue
                self._pointer = data[0]
                if self._pointer == _SENSOR_TARGET and len(data) >= 3:
                    self.target_c = int.from_bytes(data[1:3], "big", signed=True) / 100
                    self.target_writes += 1
            elif isinstance(o, Read):
                if self._pointer == _SENSOR_TEMP:
                    self._drift()
                    raw = _centi(self.temp_c).to_bytes(2, "big", signed=True)
                elif self._pointer == _SENSOR_TARGET:
                    raw = _centi(self.target_c).to_bytes(2, "big", signed=True)
                else:
                    raw = b"\x00\x00"
                out += raw[: o.n]
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
            # redact_url: child address is ${ENV}-resolved, so its content
            # isn't constrained by the expected int grammar (#126)
            raise LoadError(f"sim-i2c: invalid 7-bit I2C address "
                            f"{redact_url(str(addr))!r} (grammar: 0x03-0x77)")

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


# -- the sim device ------------------------------------------------------------------

class SimSensor(Driver, TemperatureSensor):
    """Simulated temperature sensor that ships with SHAL: reads drift, set_target is gated."""

    compatible = "shal,sim-sensor"
    kind = ByteTransport
    llm_ready = True

    def bind(self, node: Node) -> None:
        # it wraps no part: on a real bus it would talk to whatever chip answers
        # at this address, so only a shal,sim-i2c ancestor may carry it
        parent = node.parent
        while parent is not None and not isinstance(parent.driver, SimI2cBus):
            parent = parent.parent
        if parent is None:
            raise LoadError(f"{node.path}: shal,sim-sensor is a simulated device — "
                            f"put it under a shal,sim-i2c bus")
        super().bind(node)

    @idempotent  # a read: safe to auto-retry across transient drops
    @_op("Read the simulated temperature now. It drifts, so each read is a new "
        "value. Call when you need the current temperature.",
        unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(bytes([_SENSOR_TEMP])), Read(2)])
        return int.from_bytes(raw[:2], "big", signed=True) / 100

    @_op("Set the temperature the simulated room drifts toward. It changes what "
        "the sensor reports next, so it needs approval.", unit="celsius",
        side_effect="config", params={"celsius": {"minimum": -40, "maximum": 125}})
    def set_target(self, celsius: float) -> None:
        raw = _centi(celsius).to_bytes(2, "big", signed=True)
        self.bus.txn(self.addr, [Write(bytes([_SENSOR_TARGET]) + raw)])

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                               "description": "7-bit I2C address on the sim bus",
                               "examples": [72]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }


from .. import registry  # noqa: E402

registry.register(SimI2cBus)
registry.register(SimSensor)
