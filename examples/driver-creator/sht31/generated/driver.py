"""SHAL driver for the Sensirion SHT31-DIS humidity/temperature sensor.

Generated from the datasheet excerpt at
``examples/driver-creator/sht31/docs/sht31-datasheet.md``.

The SHT31 is a read-only I2C device: a single-shot, clock-stretching, high
repeatability measurement (command 0x2C 0x06) returns a 6-byte frame carrying
both temperature and relative humidity. There are no host-settable operating
parameters, so this driver declares no ``params=`` limits (datasheet section 7).
"""
from typing import Protocol, runtime_checkable

import shal
from shal import Driver, idempotent, op
from shal.transport import ByteTransport, Read, Write

# High repeatability, clock stretching enabled (datasheet section 3).
_MEASURE_HIGH = b"\x2c\x06"


@runtime_checkable
class HumiditySensor(Protocol):
    """v0.1.0 capability — relative humidity in percent (0..100 %RH)."""

    def read_humidity_percent(self) -> float: ...


@shal.register
class Sht31(Driver, shal.TemperatureSensor, HumiditySensor):
    """Sensirion SHT31-DIS digital humidity & temperature sensor (I2C).

    Temperature and humidity are first-class measurements of equal rank; each
    measurement transaction returns both in one 6-byte frame. We issue one
    transaction per op (a fresh single-shot conversion) and decode the field of
    interest, so the two ops are independent and each individually idempotent.
    """

    compatible = "sensirion,sht31"
    kind = ByteTransport
    llm_ready = True

    @idempotent
    @op(
        "Read the ambient temperature now. Triggers a fresh single-shot, high "
        "repeatability measurement and returns degrees Celsius. Call whenever a "
        "current temperature reading is needed.",
        unit="celsius",
        side_effect="none",
    )
    def read_celsius(self) -> float:
        s_t = self._measure()[0]
        # T [degC] = -45 + 175 * S_T / 65535   (datasheet section 4)
        return -45.0 + 175.0 * s_t / 65535.0

    @idempotent
    @op(
        "Read the relative humidity now. Triggers a fresh single-shot, high "
        "repeatability measurement and returns percent relative humidity "
        "(0..100 %RH). Call whenever a current humidity reading is needed.",
        unit="percent",
        side_effect="none",
    )
    def read_humidity_percent(self) -> float:
        s_rh = self._measure()[1]
        # RH [%RH] = 100 * S_RH / 65535   (datasheet section 4)
        return 100.0 * s_rh / 65535.0

    # --- helpers (underscore-prefixed: private, not wrapped/audited) ---

    def _measure(self) -> tuple[int, int]:
        """Run one single-shot measurement; return (S_T, S_RH) raw 16-bit words.

        Frame layout (datasheet section 3):
            byte0 T_MSB, byte1 T_LSB, byte2 T_CRC,
            byte3 RH_MSB, byte4 RH_LSB, byte5 RH_CRC
        In clock-stretching mode the sensor holds SCL during conversion, so the
        read follows the command with no polling.
        """
        raw = self.bus.txn(self.addr, [Write(_MEASURE_HIGH), Read(6)])
        s_t = (raw[0] << 8) | raw[1]
        s_rh = (raw[3] << 8) | raw[4]
        return s_t, s_rh

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {
                "type": "integer",
                "minimum": 0x44,
                "maximum": 0x45,
                "description": "7-bit I2C address: 0x44 (ADDR low, default) or "
                "0x45 (ADDR high).",
                "examples": [0x44],
            },
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
