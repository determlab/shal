"""Play's own reference driver for the `scpi-psu` case (issue #407 round 2,
must-fix 2) -- the same shape `arena/examples/reference_driver/driver.py`
teaches, shipped in-package so it is reachable from a real `pip install`.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class ReferencePsuDriver(Driver):
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

    @op("Enable or disable the output.", side_effect="actuator")
    def output(self, on: bool) -> None:
        self.bus.exchange(self.addr, {"scpi": f"OUTP {'ON' if on else 'OFF'}"})


registry.register(ReferencePsuDriver)
