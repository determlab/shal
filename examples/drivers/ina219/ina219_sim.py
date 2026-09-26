"""Sim twin of the TI INA219, on the ``shal,sim-i2c`` bus (moved out of
``shal.buses.sim`` with the driver, #149)."""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


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
