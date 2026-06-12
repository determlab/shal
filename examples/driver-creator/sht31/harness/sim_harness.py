"""Independent behavioral sim model for the Sensirion SHT31-DIS.

Written from examples/driver-creator/sht31/docs/sht31-datasheet.md ONLY —
deliberately independent of whatever a generation agent produces. Encodes by
INVERTING the datasheet formulas and computes real CRC-8 bytes, so a generated
driver's decode math (and optional CRC check) is validated for real.
"""
from __future__ import annotations

from typing import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


def crc8_sht(data: bytes) -> int:
    """Datasheet section 5: poly 0x31, init 0xFF, MSB-first, no reflect/xorout.

    Check value: crc8_sht(b"\\xBE\\xEF") == 0x92.
    """
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x31) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


@sim_model("sensirion,sht31")
class Sht31HarnessModel:
    """Single-shot, clock-stretching, high-repeatability model (cmd 0x2C 0x06).

    Test hooks: set ``temp_c`` / ``rh_percent``; the next measurement frame
    encodes them via the inverted datasheet formulas:
        S_T  = round((T + 45) * 65535 / 175)
        S_RH = round(RH * 65535 / 100)
    Unknown commands yield all-zero reads (which also fail any CRC check),
    making a wrong command byte sequence visible as a wrong value.
    """

    def __init__(self) -> None:
        self.temp_c = 25.0
        self.rh_percent = 50.0
        self._cmd: bytes = b""
        self._frame: bytes = b""
        self._offset = 0

    # -- model interface (sim-i2c): iterate Write/Read ops, keep command state
    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._cmd = bytes(op.data)
                self._offset = 0
                self._frame = self._measure() if self._cmd == b"\x2c\x06" else b""
            elif isinstance(op, Read):
                chunk = self._frame[self._offset:self._offset + op.n]
                chunk += b"\x00" * (op.n - len(chunk))   # NACK-ish: zero fill
                self._offset += op.n
                out += chunk
        return out

    def _measure(self) -> bytes:
        s_t = self._clamp16(round((self.temp_c + 45.0) * 65535.0 / 175.0))
        s_rh = self._clamp16(round(self.rh_percent * 65535.0 / 100.0))
        t_word = bytes([(s_t >> 8) & 0xFF, s_t & 0xFF])
        rh_word = bytes([(s_rh >> 8) & 0xFF, s_rh & 0xFF])
        return (t_word + bytes([crc8_sht(t_word)])
                + rh_word + bytes([crc8_sht(rh_word)]))

    @staticmethod
    def _clamp16(value: int) -> int:
        return max(0, min(0xFFFF, value))
