"""ti,ina219 — I2C bus-voltage / current / power monitor (PowerMonitor).

Wave 1 onboarding driver (#2): a clean second I2C device on top of the shipped
ByteTransport buses (real i2c-cli or the sim). Bus voltage from register 0x02;
current from register 0x04 (assumes a calibrated 100 uA/LSB, the common default).
"""
from __future__ import annotations

from .. import registry
from ..capabilities import PowerMonitor
from ..driver import Driver, idempotent, op
from ..transport import ByteTransport, Read, Write

_BUS_VOLTAGE = 0x02
_CURRENT = 0x04
_BUS_LSB = 0.004        # 4 mV per LSB, value in bits 15:3
_CURRENT_LSB = 0.0001   # 100 uA per LSB (depends on the calibration register)


@registry.register
class Ina219(Driver, PowerMonitor):
    compatible = "ti,ina219"
    kind = ByteTransport
    llm_ready = True

    def _read16(self, reg: int) -> int:
        raw = self.bus.txn(self.addr, [Write(bytes([reg])), Read(2)])
        return (raw[0] << 8) | raw[1]

    @idempotent
    @op("Read the bus voltage now. Call when you need this rail's voltage.",
        unit="volt", side_effect="none")
    def read_voltage(self) -> float:
        return (self._read16(_BUS_VOLTAGE) >> 3) * _BUS_LSB

    @idempotent
    @op("Read the current through the shunt now.", unit="ampere", side_effect="none")
    def read_current(self) -> float:
        raw = self._read16(_CURRENT)
        if raw >= 0x8000:  # 16-bit two's complement
            raw -= 0x10000
        return raw * _CURRENT_LSB

    @idempotent
    @op("Read the power draw now (bus voltage times current).",
        unit="watt", side_effect="none")
    def read_power(self) -> float:
        return self.read_voltage() * self.read_current()

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail
        return {
            "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                               "description": "7-bit I2C address", "examples": [64]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
