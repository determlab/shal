"""Sim twin of the NXP PCA9548 I2C mux, on the ``shal,sim-i2c`` bus (moved out
of ``shal.buses.sim`` with the driver, #149)."""
from collections.abc import Sequence

from shal.buses.sim import sim_model
from shal.transport import Op, Read, Write


@sim_model("nxp,pca9548")
class Pca9548Model:
    """Control-register model; counts selects for cache regression tests."""

    def __init__(self) -> None:
        self.control = 0
        self.select_count = 0

    def txn(self, ops: Sequence[Op]) -> bytes:
        out = b""
        for op in ops:
            if isinstance(op, Write):
                self.control = op.data[0] if op.data else 0
                self.select_count += 1
            elif isinstance(op, Read):
                out += bytes([self.control])[: op.n]
        return out
