"""microchip,mcp23017 — 16-bit I2C GPIO expander (GPIOExpander).

Pins 0-7 are port A, 8-15 port B. Registers (bank 0): IODIR 0x00/0x01
(1=input, 0=output), GPIO 0x12/0x13, OLAT 0x14/0x15. Set/write are
read-modify-write on the relevant port register.
"""
from __future__ import annotations

from .. import registry
from ..capabilities import GPIOExpander
from ..driver import Driver, idempotent, op
from ..transport import ByteTransport, Read, Write

_IODIR = 0x00
_GPIO = 0x12
_OLAT = 0x14


@registry.register
class Mcp23017(Driver, GPIOExpander):
    compatible = "microchip,mcp23017"
    kind = ByteTransport
    llm_ready = True

    def _read(self, reg: int) -> int:
        return self.bus.txn(self.addr, [Write(bytes([reg])), Read(1)])[0]

    def _write(self, reg: int, val: int) -> None:
        self.bus.txn(self.addr, [Write(bytes([reg, val & 0xFF]))])

    @op("Set a pin (0-15) as output (true) or input (false).", side_effect="write")
    def set_direction(self, pin: int, output: bool) -> None:
        reg, bit = _IODIR + pin // 8, pin % 8
        cur = self._read(reg)
        self._write(reg, cur & ~(1 << bit) if output else cur | (1 << bit))

    @op("Drive an output pin (0-15) high (true) or low (false).",
        side_effect="actuator")
    def write_pin(self, pin: int, high: bool) -> None:
        reg, bit = _OLAT + pin // 8, pin % 8
        cur = self._read(reg)
        self._write(reg, cur | (1 << bit) if high else cur & ~(1 << bit))

    @idempotent
    @op("Read the level of pin (0-15) now.", side_effect="none")
    def read_pin(self, pin: int) -> bool:
        reg, bit = _GPIO + pin // 8, pin % 8
        return bool(self._read(reg) & (1 << bit))

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                               "description": "7-bit I2C address", "examples": [32]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
