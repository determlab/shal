"""Behavioral sim model for the Sensirion SHT31-DIS, on the ``shal,sim-i2c`` bus.

Written from the same datasheet as the driver (NOT a copy of the driver's
decode math). The model holds raw 16-bit signal words ``S_T`` and ``S_RH`` as
internal state, implements the single-shot measurement command grammar, and
emits a 6-byte frame with correct CRC bytes (datasheet sections 3-5).

Tests set the raw words directly (``model.s_t`` / ``model.s_rh``) to inject the
datasheet's worked-example vectors.
"""
from shal.transport import Read, Write
from shal.buses.sim import sim_model

# CRC-8: poly 0x31, init 0xFF, no reflect, no final XOR (datasheet section 5).
# Check value: CRC(0xBE, 0xEF) == 0x92.
_POLY = 0x31


def _crc8(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ _POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


@sim_model("sensirion,sht31")
class Sht31Sim:
    """Stateful SHT31 device model.

    Default state corresponds to the datasheet's complete-frame example
    (T = 25.0 degC, RH ~= 50.0008 %RH): S_T = 0x6666, S_RH = 0x8000.
    """

    def __init__(self) -> None:
        self.s_t = 0x6666   # T = 25.0 degC
        self.s_rh = 0x8000  # RH ~= 50.0008 %RH
        self._armed = False

    def _frame(self) -> bytes:
        t_hi, t_lo = (self.s_t >> 8) & 0xFF, self.s_t & 0xFF
        rh_hi, rh_lo = (self.s_rh >> 8) & 0xFF, self.s_rh & 0xFF
        return bytes(
            [
                t_hi, t_lo, _crc8(bytes([t_hi, t_lo])),
                rh_hi, rh_lo, _crc8(bytes([rh_hi, rh_lo])),
            ]
        )

    def txn(self, ops) -> bytes:
        """Process one I2C transaction (sequence of Write/Read ops).

        A write of 0x2C 0x06 arms a high-repeatability conversion; a subsequent
        Read returns the 6-byte frame (clock-stretching is implicit). The frame
        is also produced for a bare read after arming, matching the device's
        repeated-start write-then-read sequence.
        """
        out = bytearray()
        for o in ops:
            if isinstance(o, Write):
                if bytes(o.data) == b"\x2c\x06":
                    self._armed = True
                # other commands (medium/low repeatability) would arm too, but
                # the driver only issues high repeatability.
            elif isinstance(o, Read):
                frame = self._frame()
                # Return exactly the requested length (6 for this device).
                out.extend(frame[: o.n])
                self._armed = False
        return bytes(out)
