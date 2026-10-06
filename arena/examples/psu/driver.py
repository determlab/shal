"""Minimal driver for the ``scpi-psu`` case: one setpoint op and one read op.
Copy it, keep ``compatible`` exactly as written (the case fixes it), and
adapt the SCPI strings to the datasheet the run's card printed."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class MinimalPsu(Driver):
    compatible = "arena,bench-psu1"
    kind = MessageTransport
    llm_ready = True

    @idempotent  # an absolute setpoint: re-sending the same volts is safe
    @op("Set the PSU's output voltage (absolute setpoint).", unit="volt",
        side_effect="actuator", params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> None:
        self.bus.exchange(self.addr, {"scpi": f"VOLT {volts}"})

    @idempotent
    @op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT?", "query": True})
        return float(reply["reply"])


registry.register(MinimalPsu)
