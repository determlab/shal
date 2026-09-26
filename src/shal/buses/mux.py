"""The I2C mux mechanism — a node that is also a bus (DESIGN V2 'Muxes').

One MuxChannel bus instance per channel; ALL channels of one physical mux share
one per-mux state object (the v1 cross-mux cache-poisoning bug, fixed by design).
Explicit per-kind delegation, selection inside the call, under state.lock —
no __getattr__ magic; attribute access stays side-effect-free.

This is the mechanism, and it ships. A mux chip is a vendor part, so it does not:
the PCA9548 driver built on it lives in ``examples/drivers/pca9548/`` (#149).
"""
from __future__ import annotations

import threading
from collections.abc import Sequence
from typing import Any

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
