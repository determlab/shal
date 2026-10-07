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
from ..log import bus_logger, current_txn, record_exchange, redact, redact_url
from ..node import Node
from ..transport import ByteTransport, Op, Read, Transport, Write
from .sim_fault import SimFaultMixin

# -- device models -------------------------------------------------------------

SIM_MODELS: dict[str, type] = {}


def sim_model(compatible: str):
    def deco(cls):
        SIM_MODELS[compatible] = cls
        return cls
    return deco


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

class SimI2cBus(SimFaultMixin, Driver, Transport, ByteTransport):
    """A node that provides ByteTransport to its children — entirely in memory."""

    compatible = "shal,sim-i2c"
    kind = None  # may sit at root, or behind any CommandTransport later
    simulated = True  # issue #347 round 2: a real driver on this bus is still a sim

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[int, Any] = {}
        self._unmodeled: dict[int, str] = {}   # declared children with no sim model
        self._init_fault()   # fault: unplugged / SHAL_SIM_UNPLUG / after: N (#304, #349)
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
            if isinstance(node.address, int):
                if model is not None:
                    self._models.setdefault(node.address, model())
                elif isinstance(comp, str):
                    self._unmodeled.setdefault(node.address, comp)
                self._register_fault(node)
        super().activate()
        self.log.debug("connect (%d device models)", len(self._models),
                       event="connect")

    def model_for(self, addr: int) -> Any:
        self.ensure_ready()
        try:
            return self._models[addr]
        except KeyError:
            have = ", ".join(f"0x{a:02x}" for a in sorted(self._models)) or "none"
            raise LookupError(f"sim-i2c: no sim model at {addr!r}; "
                              f"addresses with models: {have}") from None

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
            if self._faulted(addr):  # fault: unplugged / SHAL_SIM_UNPLUG (#304, #349)
                raise HopError(f"no answer from the device at 0x{addr:02x}",
                               path=self.host.path, hop="sim-i2c",
                               txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                hint = ""
                if addr in self._unmodeled:
                    comp = self._unmodeled[addr]
                    hint = (f" — no sim model registered for {comp!r}; "
                            f"decorate a class with @sim_model({comp!r})")
                raise HopError(f"i2c NAK at 0x{addr:02x}{hint}", path=self.host.path,
                               hop="sim-i2c", txn=current_txn.get())
            result = model.txn(ops)
            if self.log.isEnabledFor(logging.DEBUG):  # hot path costs nothing when off
                self.log.debug("txn -> %s", redact(result),
                               event="txn", addr=hex(addr))
            record_exchange("sim_i2c", self.host.path, addr, ops, result)
            return result


# -- the sim device ------------------------------------------------------------------

class SimSensor(Driver, TemperatureSensor):
    """Simulated temperature sensor that ships with SHAL: reads drift, set_target is gated."""

    compatible = "shal,sim-sensor"
    kind = ByteTransport
    llm_ready = True
    simulated = True  # issue #347: a SHAL simulator, never a real chip

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
