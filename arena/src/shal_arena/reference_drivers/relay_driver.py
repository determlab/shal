"""Play's own reference driver for the `relay-modbus` case (issue #407
round 2, must-fix 2) -- the same shape `arena/examples/minimal_relay_driver.py`
teaches, shipped in-package so it is reachable from a real `pip install`.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class ReferenceRelayDriver(Driver):
    compatible = "arena,bench-relay1"
    kind = MessageTransport
    llm_ready = True

    @idempotent  # a read: safe to retry
    @op("Read whether this relay channel is energized (closed) now.",
        side_effect="none", params={"channel": {"minimum": 0, "maximum": 7}})
    def read_relay(self, channel: int) -> bool:
        reply = self.bus.exchange(self.addr, {"fc": 1, "address": channel, "count": 1})
        return bool(reply["bits"][0])

    @idempotent  # an absolute state: resending the same on/off is safe
    @op("Switch one relay channel on (closed) or off (open).",
        side_effect="write", params={"channel": {"minimum": 0, "maximum": 7}})
    def set_relay(self, channel: int, on: bool) -> None:
        self.bus.exchange(self.addr, {"fc": 5, "address": channel, "value": bool(on)})


registry.register(ReferenceRelayDriver)
