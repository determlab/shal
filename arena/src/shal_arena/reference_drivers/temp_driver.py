"""Play's own reference driver for the `sht31` case (issue #407 round 2,
must-fix 2) -- the same shape `arena/examples/minimal_temp_driver.py`
teaches, shipped in-package so it is reachable from a real `pip install`.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import ByteTransport, Read, Write


class ReferenceTempDriver(Driver):
    compatible = "arena,bench-temp1"
    kind = ByteTransport
    llm_ready = True

    @idempotent  # a read: safe to retry
    @op("Read the measured temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(bytes([0x2C, 0x06])), Read(6)])
        t_raw = int.from_bytes(raw[0:2], "big")
        return -45.0 + 175.0 * t_raw / 65535.0


registry.register(ReferenceTempDriver)
