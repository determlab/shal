"""ti,ads1115 — 16-bit I2C ADC (ADC capability).

Single-shot conversions, single-ended, ±4.096 V full scale. read_voltage(channel)
writes the config register to start a conversion on AIN<channel>, then reads the
signed conversion register and scales to volts.
"""
from __future__ import annotations

from .. import registry
from ..capabilities import ADC
from ..driver import Driver, idempotent, op
from ..transport import ByteTransport, Read, Write

_CONVERSION = 0x00
_CONFIG = 0x01
_FSR = 4.096  # ±4.096 V (PGA gain 1)


@registry.register
class Ads1115(Driver, ADC):
    compatible = "ti,ads1115"
    kind = ByteTransport
    llm_ready = True

    @idempotent
    @op("Read the voltage on an input channel (0-3) now.",
        unit="volt", side_effect="none")
    def read_voltage(self, channel: int = 0) -> float:
        if not 0 <= channel <= 3:
            raise ValueError("ads1115 channel must be 0-3")
        # OS=start | MUX=single-ended AIN<ch> | PGA=±4.096 | single-shot | 128SPS | comp off
        cfg = 0x8000 | ((0x4 | channel) << 12) | 0x0200 | 0x0100 | 0x0080 | 0x0003
        self.bus.txn(self.addr,
                     [Write(bytes([_CONFIG, (cfg >> 8) & 0xFF, cfg & 0xFF]))])
        raw = self.bus.txn(self.addr, [Write(bytes([_CONVERSION])), Read(2)])
        val = (raw[0] << 8) | raw[1]
        if val >= 0x8000:              # 16-bit two's complement
            val -= 0x10000
        return val * _FSR / 32768

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                               "description": "7-bit I2C address", "examples": [72]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
