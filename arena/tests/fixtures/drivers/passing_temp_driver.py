"""A `driver.py` that passes the `sht31` case's ADK-style check, re-importable
many times in one process without a registry collision (`override=True`,
same reasoning as `passing_dmm_driver.py`)."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import ByteTransport, Read, Write


class BenchTemp1(Driver):
    compatible = "arena,bench-temp1"
    kind = ByteTransport
    llm_ready = True

    @idempotent
    @op("Read the measured temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(bytes([0x2C, 0x06])), Read(6)])
        t_raw = int.from_bytes(raw[0:2], "big")
        return -45.0 + 175.0 * t_raw / 65535.0


registry.register(BenchTemp1, override=True)
