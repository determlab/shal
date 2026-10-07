"""Minimal ``driver.py`` for a `probe`-style instrument on I2C (the `sht31`
ADK case; issue relay-rail). Nothing beyond what `sht31`'s own datasheet
(``arena/src/shal_arena/adk/sht31/docs/datasheet.md``, also printed by
``shal-arena run``'s own JSON) actually needs: write the measurement
command, read the 6-byte frame, convert the temperature word (the CRC
check is optional per the datasheet, so this driver skips it).
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import ByteTransport, Read, Write


class MinimalTempDriver(Driver):
    compatible = "arena,bench-temp1"
    kind = ByteTransport
    llm_ready = True

    @idempotent  # a read: safe to retry
    @op("Read the measured temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(bytes([0x2C, 0x06])), Read(6)])
        t_raw = int.from_bytes(raw[0:2], "big")
        return -45.0 + 175.0 * t_raw / 65535.0


# issue #407: override=True -- see minimal_dmm_driver.py's own comment on
# this line: a second in-process call re-execs this file fresh, and without
# override=True the registry can't resolve the resulting 2 candidates.
registry.register(MinimalTempDriver, override=True)
