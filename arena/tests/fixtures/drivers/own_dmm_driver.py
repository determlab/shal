"""A `dmm` driver written from the datasheet, not copied from the packaged
reference (issue #487): its own command table, a query helper, and its own
parsing of the reply. `override=True` for the same reason as the other
fixtures here: tests import drivers in any order."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport

COMMANDS = {"dc_volts": "MEAS:VOLT:DC?"}


class HandWrittenMeter(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    def _ask(self, key: str) -> str:
        answer = self.bus.exchange(self.addr, {"scpi": COMMANDS[key], "query": True})
        text = str(answer.get("reply", "")).strip()
        if not text:
            raise ValueError(f"empty reply to {COMMANDS[key]}")
        return text

    @idempotent
    @op("DC voltage at the meter's input, in volts.", unit="volt", side_effect="none")
    def dc_volts(self) -> float:
        value = float(self._ask("dc_volts").split(",")[0])
        return value


registry.register(HandWrittenMeter, override=True)
