"""The fault hook every sim bus shares (issue #304, #349): `fault: unplugged`
(or the `SHAL_SIM_UNPLUG=<node id>` env var) makes a node refuse every hop with
a `HopError(delivered="no")`, exactly as a real disconnected link would. The
optional `after: <N>` node key lets the device answer N calls normally first,
then drop from the next call on — so a demo can "pull the cable" mid-run.

One mixin, one place that decides "is this call faulted"; each bus still
builds its own `HopError` (the address format and the bus name differ), but
none of them re-implements the env var, the node-key, or the call counting.
"""
from __future__ import annotations

import os
from typing import Any

from ..node import Node


class SimFaultMixin:
    """Mixed into a sim bus alongside `Driver`/`Transport`. The bus calls
    `_init_fault()` once (in `__init__`), `_register_fault(node)` for every
    child node it walks at `activate()`, and `_faulted(addr)` at the top of
    its `exchange`/`txn`, before talking to the address, to decide whether
    this call should refuse."""

    def _init_fault(self) -> None:
        self._fault_after: dict[Any, int | None] = {}  # addr -> after N; None: every call
        self._fault_calls: dict[Any, int] = {}          # addr -> calls seen so far

    def _register_fault(self, node: Node) -> None:
        unplug_id = os.environ.get("SHAL_SIM_UNPLUG")
        spec = getattr(node, "spec", {}) or {}
        if spec.get("fault") == "unplugged" or (
                unplug_id is not None and node.id == unplug_id):
            self._fault_after[node.address] = spec.get("after")

    def _faulted(self, addr: Any) -> bool:
        if addr not in self._fault_after:
            return False
        after = self._fault_after[addr]
        if after is None:
            return True
        seen = self._fault_calls.get(addr, 0)
        self._fault_calls[addr] = seen + 1
        return seen >= after
