"""Minimal ``driver.py`` for a `probe`-style instrument (the `dmm` ADK case;
issue #396). Nothing beyond what `dmm`'s own datasheet
(``arena/src/shal_arena/adk/dmm/docs/datasheet.md``, also printed by
``shal-arena run``'s own JSON) actually needs: one read. Copy this file,
change the SCPI text if your case's datasheet differs, and you have a
working driver -- see arena/README.md's "Write your driver" section for the
two commands that check and then play it.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class MinimalDmmDriver(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent  # a read: safe to retry
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(MinimalDmmDriver)
