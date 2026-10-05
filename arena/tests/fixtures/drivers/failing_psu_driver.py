"""A `driver.py` that FAILS the `scpi-psu` case's ADK-style check: it never
sets `llm_ready = True`, which `conformance.check_driver`'s static checks
flag as a problem for any non-Transport device driver. Used to prove `check`
does not light the tile when the driver has problems."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BrokenBenchPsu1(Driver):
    compatible = "arena,bench-psu1"
    kind = MessageTransport
    # llm_ready deliberately left unset

    @idempotent
    @op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT?", "query": True})
        return float(reply["reply"])


registry.register(BrokenBenchPsu1, override=True)
