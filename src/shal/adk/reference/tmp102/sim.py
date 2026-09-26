"""Sim twin of the TI TMP102, on the ``shal,sim-i2c`` bus.

The model holds the temperature as state and answers the register grammar the
chip does: write the pointer, then read two bytes. Import this module next to
``driver.py`` (``--drivers``) and a ``shal,sim-i2c`` bus builds one per node.
"""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


@sim_model("ti,tmp102")
class Tmp102Model:
    def __init__(self) -> None:
        self.temp_c = 25.0
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._pointer = op.data[0] if op.data else self._pointer
            elif isinstance(op, Read):
                if self._pointer == 0:  # temperature register, 12-bit, 0.0625 C/LSB
                    raw = int(self.temp_c / 0.0625) & 0xFFF
                    out += bytes([(raw >> 4) & 0xFF, (raw & 0xF) << 4])[: op.n]
                else:
                    out += b"\x00" * op.n
        return out
