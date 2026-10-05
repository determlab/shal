"""A `driver.py` that passes the `dmm` case's ADK-style check. `override=True`
on registration mirrors `passing_psu_driver.py`: re-importing this file in a
later test replaces whatever `arena,bench-dmm1` candidate an earlier test's
fixture left behind, so tests can run in any order."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BenchDmm1(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(BenchDmm1, override=True)
