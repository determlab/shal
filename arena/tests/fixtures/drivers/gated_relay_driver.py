"""Test fixture (issue relay-rail): a `relay-modbus` driver with one
deliberately gated, out-of-range-capable op, used to prove `shal-arena
call` really does run through SHAL's normal gate and limits — not a
second, arena-local mechanism that only ever says yes."""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, op
from shal.transport import MessageTransport


class GatedRelayDriver(Driver):
    compatible = "arena,bench-relay1"
    kind = MessageTransport
    llm_ready = True

    @op("Read whether this relay channel is energized (closed) now.",
        side_effect="none", params={"channel": {"minimum": 0, "maximum": 7}})
    def read_relay(self, channel: int) -> bool:
        reply = self.bus.exchange(self.addr, {"fc": 1, "address": channel, "count": 1})
        return bool(reply["bits"][0])

    @op("Force one relay channel on or off, no questions asked.",
        side_effect="actuator", params={"channel": {"minimum": 0, "maximum": 7}})
    def force_relay(self, channel: int, on: bool) -> None:
        self.bus.exchange(self.addr, {"fc": 5, "address": channel, "value": bool(on)})


registry.register(GatedRelayDriver, override=True)
