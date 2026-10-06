"""Minimal ``driver.py`` for a `drives`-style instrument (the `scpi-psu` ADK
case; issue #396). Nothing beyond what `scpi-psu`'s own datasheet
(``arena/src/shal_arena/adk/scpi-psu/docs/datasheet.md``, also printed by
``shal-arena run``'s own JSON) actually needs: a setpoint write and a
readback. Copy this file, change the SCPI text if your case's datasheet
differs, and you have a working driver -- see arena/README.md's "Write your
driver" section for the two commands that check and then play it.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class MinimalPsuDriver(Driver):
    compatible = "arena,bench-psu1"
    kind = MessageTransport
    llm_ready = True

    @idempotent  # an absolute setpoint: resending the same volts is safe
    @op("Set the PSU's output voltage (absolute setpoint).", unit="volt",
        side_effect="actuator", params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> None:
        self.bus.exchange(self.addr, {"scpi": f"VOLT {volts}"})

    @idempotent  # a read: safe to retry
    @op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT?", "query": True})
        return float(reply["reply"])


registry.register(MinimalPsuDriver)
