"""Sim twin of the Microchip MCP9808, on the ``shal,sim-i2c`` bus (moved out of
``shal.buses.sim`` with the driver, #149)."""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


@sim_model("microchip,mcp9808")
class Mcp9808Model:
    def __init__(self) -> None:
        self.temp_c = 22.5
        self._pointer = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self._pointer = op.data[0] if op.data else self._pointer
            elif isinstance(op, Read):
                if self._pointer == 0x05:          # 13-bit, 0.0625 C/LSB, sign bit12
                    val = int(round(self.temp_c * 16))
                    if val < 0:
                        val = 0x2000 + val
                    out += bytes([(val >> 8) & 0x1F, val & 0xFF])[: op.n]
                else:
                    out += b"\x00" * op.n
        return out
