"""A `driver.py` that passes the `scpi-psu` case's ADK-style check. `override=True`
on registration makes re-importing this file in a later test replace whatever
`arena,bench-psu1` candidate an earlier test's fixture left behind, so tests
can run in any order without an "ambiguous compatible" collision."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BenchPsu1(Driver):
    compatible = "arena,bench-psu1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Set the PSU's output voltage (absolute setpoint).", unit="volt",
        side_effect="actuator", params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> None:
        self.bus.exchange(self.addr, {"scpi": f"VOLT {volts}"})

    @idempotent
    @op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT?", "query": True})
        return float(reply["reply"])

    @op("Enable or disable the output.", side_effect="actuator")
    def output(self, on: bool) -> None:
        self.bus.exchange(self.addr, {"scpi": f"OUTP {'ON' if on else 'OFF'}"})


registry.register(BenchPsu1, override=True)
