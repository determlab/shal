"""Sim twin of the TI ADS1115, on the ``shal,sim-i2c`` bus (moved out of
``shal.buses.sim`` with the driver, #149)."""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


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
