"""Minimal driver for the ``dmm`` case: one zero-argument read op. ``bench``
and ``measure`` call the one op with ``side_effect="none"`` and no params.
Keep ``compatible`` exactly as written (the case fixes it)."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class MinimalDmm(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(MinimalDmm)
