"""Sim twin of the Microchip MCP23017, on the ``shal,sim-i2c`` bus.

A register file: a write sets the pointer (and, with a second byte, that
register); a read of GPIOA/GPIOB returns the output latch (OLATA/OLATB), so a
pin driven high reads back high. Import it next to ``driver.py`` (``--drivers``).
"""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


@sim_model("microchip,mcp23017")
class Mcp23017Model:
    def __init__(self) -> None:
        self.regs = {0x00: 0xFF, 0x01: 0xFF}   # IODIRA/B default all inputs
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                data = op.data
                if not data:
                    continue
                self._pointer = data[0]
                if len(data) >= 2:
                    self.regs[self._pointer] = data[1]
            elif isinstance(op, Read):
                reg = self._pointer
                if reg in (0x12, 0x13):            # GPIO reads loop back OLAT
                    reg += 2
                out += bytes([self.regs.get(reg, 0)])[: op.n]
        return out
