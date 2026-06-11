"""microchip,mcp9808 — ±0.25 C I2C temperature sensor (TemperatureSensor).

Ambient temperature register 0x05: 13-bit, 0.0625 C/LSB, sign at bit 12; the
upper byte's top three bits are alarm flags and are masked off.
"""
from __future__ import annotations

from .. import registry
from ..capabilities import TemperatureSensor
from ..driver import Driver, idempotent, op
from ..transport import ByteTransport, Read, Write

_AMBIENT = 0x05


@registry.register
class Mcp9808(Driver, TemperatureSensor):
    compatible = "microchip,mcp9808"
    kind = ByteTransport
    llm_ready = True

    @idempotent
    @op("Read the ambient temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(bytes([_AMBIENT])), Read(2)])
        upper = raw[0] & 0x1F          # mask the alarm flags
        if upper & 0x10:               # sign bit set -> negative
            upper &= 0x0F
            return (upper * 16 + raw[1] / 16.0) - 256
        return upper * 16 + raw[1] / 16.0

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                               "description": "7-bit I2C address", "examples": [24]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
