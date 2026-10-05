"""Reference driver for the ``scpi-psu`` ADK case (issue #346): copy this
file as the starting point for your own ``driver.py`` and adapt it to
whichever ``case:`` your task's instrument names
(``arena/src/shal_arena/adk/<case>/docs/datasheet.md`` is the command
reference for each packaged case; ``scpi-psu``'s is at
``adk/scpi-psu/docs/datasheet.md``).

Bind this against a run's ``psu0``-style instrument with:

    shal-arena check-driver <run-id> psu0 examples/reference_driver/driver.py --json

A correct driver registers the case's ``compatible`` string and implements
the ops the datasheet suggests, same shape `shal`'s own reference drivers
use (``shal docs --example tmp102``; AGENTS.md). Nothing here is special to
this one card or task — only the ``compatible`` string and SCPI dialect tie
it to ``scpi-psu``.
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
