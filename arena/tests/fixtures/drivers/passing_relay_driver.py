"""A `driver.py` that passes the `relay-modbus` case's ADK-style check, and
can be re-imported many times in one process without an "ambiguous
compatible" collision (`override=True`, same reasoning as
`passing_dmm_driver.py`) — needed here because `test_relay_rail.py` calls
`call_op` (which re-imports its driver file on every call, same as
`measure`/`check`/`drive`) several times per test."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BenchRelay1(Driver):
    compatible = "arena,bench-relay1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read whether this relay channel is energized (closed) now.",
        side_effect="none", params={"channel": {"minimum": 0, "maximum": 7}})
    def read_relay(self, channel: int) -> bool:
        reply = self.bus.exchange(self.addr, {"fc": 1, "address": channel, "count": 1})
        return bool(reply["bits"][0])

    @idempotent
    @op("Switch one relay channel on (closed) or off (open).",
        side_effect="write", params={"channel": {"minimum": 0, "maximum": 7}})
    def set_relay(self, channel: int, on: bool) -> None:
        self.bus.exchange(self.addr, {"fc": 5, "address": channel, "value": bool(on)})


registry.register(BenchRelay1, override=True)
