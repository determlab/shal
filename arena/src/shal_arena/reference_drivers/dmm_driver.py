"""Play's own reference driver for the `dmm` case (issue #407 round 2,
must-fix 2) -- the same shape `arena/examples/minimal_dmm_driver.py`
teaches, shipped in-package so it is reachable from a real `pip install`.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class ReferenceDmmDriver(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent  # a read: safe to retry
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(ReferenceDmmDriver)
