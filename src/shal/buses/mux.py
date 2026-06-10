"""nxp,pca9548 — a node that is also a bus (DESIGN V2 'Muxes').

One MuxChannel bus instance per channel; ALL channels of one physical mux share
one per-mux state object (the v1 cross-mux cache-poisoning bug, fixed by design).
Explicit per-kind delegation, selection inside the call, under state.lock —
no __getattr__ magic; attribute access stays side-effect-free.
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

from .. import registry
from ..driver import Driver
from ..errors import LoadError
from ..log import bus_logger
from ..node import Node
from ..transport import ByteTransport, Op, Transport, Write


class MuxState:
    """Shared across this mux's channels only — never lives on the parent bus."""

    def __init__(self) -> None:
        self.selected: int | None = None
        self.lock = threading.RLock()  # guards check -> select -> talk


class MuxChannel(Transport, ByteTransport):
    def __init__(self, host: Node, *, upstream: Transport, state: MuxState,
                 channel: int, mux_addr: int) -> None:
        Transport.__init__(self, host)
        self._upstream_bus = upstream
        self.state = state
        self.channel = channel
        self.mux_addr = mux_addr
        self.log = bus_logger("mux", host.path)

    @property
    def upstream(self) -> Transport:
        return self._upstream_bus  # the bus the mux sits on, not host.parent_bus

    def is_active(self) -> bool:
        return self.state.selected == self.channel

    def activate(self) -> None:
        self.log.debug("select ch%d (was %s)", self.channel, self.state.selected,
                       event="select", addr=f"0x{self.mux_addr:02x}")
        self.upstream.txn(self.mux_addr, [Write(bytes([1 << self.channel]))])
        self.state.selected = self.channel

    def validate_address(self, addr: Any) -> None:
        self.upstream.validate_address(addr)  # downstream grammar = upstream's

    def txn(self, addr: int, ops: Sequence[Op]) -> bytes:
        with self.state.lock:  # check -> select -> talk, atomically per mux
            self.ensure_ready()
            return self.upstream.txn(addr, ops)

    def close(self) -> None:
        if self.state.selected == self.channel:
            self.state.selected = None


@registry.register
class Pca9548(Driver):
    """The mux driver itself is NOT a Transport; it provides one bus per channel."""

    compatible = "nxp,pca9548"
    kind = ByteTransport
    N_CHANNELS = 8

    def __init__(self) -> None:
        self._state = MuxState()  # per physical mux

    def provide_child_bus(self, child: Node) -> Transport:
        ch = child.address
        if not isinstance(ch, int) or not (0 <= ch < self.N_CHANNELS):
            raise LoadError(f"{child.path}: pca9548 channel must be 0-"
                            f"{self.N_CHANNELS - 1}, got {ch!r}")
        return MuxChannel(child, upstream=self.bus, state=self._state,
                          channel=ch, mux_addr=self.addr)
