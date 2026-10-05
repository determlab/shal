"""A `driver.py` that FAILS the `dmm` case's ADK-style check: it never sets
`llm_ready = True`, which `conformance.check_driver`'s static checks flag as
a problem for any non-Transport device driver. Used to prove `check-driver`
does not light the tile when the driver has problems (issue #311 Done-when:
"a broken one fails with a message naming the fix")."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BrokenBenchDmm1(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    # llm_ready deliberately left unset

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(BrokenBenchDmm1, override=True)
