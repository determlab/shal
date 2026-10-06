"""issue #432: a driver whose read op raises its own bug (never touches the
bus) -- the "driver" failure cause, as opposed to a transport failure
(the `open` fault, which never reaches the driver's own code at all)."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BuggyDmm1(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        raise ValueError("this driver has a bug and never reaches the bus")


registry.register(BuggyDmm1, override=True)
