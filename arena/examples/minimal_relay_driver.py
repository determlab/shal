"""Minimal ``driver.py`` for the `relay-modbus` ADK case (issue relay-rail):
the coil read/write commands its own datasheet
(``arena/src/shal_arena/adk/relay-modbus/docs/datasheet.md``, also printed
by ``shal-arena run``'s own JSON) documents, exchanged as plain dicts over
``shal,sim-msg`` — no ``pymodbus``, no TCP, no byte-level Modbus framing.
Run any op through SHAL with the generic runner path:

    shal-arena call <run-id> relay0 examples/minimal_relay_driver.py \\
        set_relay 0 false --json
    shal-arena call <run-id> relay0 examples/minimal_relay_driver.py \\
        read_relay 0 --json
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class MinimalRelayDriver(Driver):
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


registry.register(MinimalRelayDriver)
