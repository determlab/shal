"""ti,tmp102 — the canonical first driver (DESIGN V2 'Capabilities')."""
from __future__ import annotations

from .. import registry
from ..capabilities import TemperatureSensor
from ..driver import Driver, idempotent, op
from ..transport import ByteTransport, Read, Write


@registry.register
class Tmp102(Driver, TemperatureSensor):
    compatible = "ti,tmp102"
    kind = ByteTransport
    llm_ready = True

    @idempotent  # a read: safe to auto-retry across transient drops
    @op("Read the current ambient temperature. Call when you need this sensor's "
        "temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return ((raw[0] << 4) | (raw[1] >> 4)) * 0.0625
