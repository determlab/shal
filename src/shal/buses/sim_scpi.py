"""shal,sim-scpi — simulated SCPI instrument rack (issue #10).

The MessageTransport twin of ``shal,sim-i2c``: device models are built from the
children's ``compatible`` at activation and answer the SAME ``{"scpi": cmd,
"query": bool} -> {"reply": text}`` contract as ``shal,scpi-raw`` — so a SCPI
driver runs UNCHANGED against the sim or the real instrument (test before you
touch the real supply).

A model is a class registered with ``@scpi_sim_model("vendor,part")`` exposing
``scpi(cmd: str) -> str`` (return "" for writes). One model instance per child
node, keyed by the child's address.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..driver import Driver
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn, redact_url
from ..node import Node
from ..transport import MessageTransport, Transport

logger = logging.getLogger("shal.bus.sim_scpi")

SCPI_SIM_MODELS: dict[str, type] = {}


def scpi_sim_model(compatible: str):
    """Register a SCPI device model for ``shal,sim-scpi`` (mirrors sim-i2c's
    ``@sim_model``). The model class needs one method: ``scpi(cmd) -> str``."""
    def deco(cls):
        SCPI_SIM_MODELS[compatible] = cls
        return cls
    return deco


class SimScpiBus(Driver, Transport, MessageTransport):
    """A node that provides MessageTransport (scpi-raw dialect) to its children —
    entirely in memory."""

    compatible = "shal,sim-scpi"
    kind = None  # may sit at root, or behind any CommandTransport later

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[Any, Any] = {}
        self.fail_next: int = 0          # test hook: fail N next txns (delivered=no)
        self.fail_delivered_unknown = False  # test hook: ambiguous failure
        self.connect_count = 0
        self.log = bus_logger("sim_scpi", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, (str, int)) or str(addr) == "":
            # redact_url: child address is ${ENV}-resolved, so its content
            # isn't constrained by the expected label grammar (#126)
            raise LoadError(f"sim-scpi: child address must be a non-empty "
                            f"instrument/channel label, got "
                            f"{redact_url(str(addr))!r}")

    def activate(self) -> None:
        self.connect_count += 1
        for node in self.host.walk():
            if node is self.host:
                continue
            comp = getattr(node, "spec", {}).get("driver")
            model = SCPI_SIM_MODELS.get(comp)
            if model is not None and node.address is not None:
                self._models.setdefault(node.address, model())
        super().activate()
        self.log.debug("connect (%d instrument models)", len(self._models),
                       event="connect")

    def model_for(self, addr: Any):
        self.ensure_ready()
        return self._models[addr]

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:  # check -> activate -> talk, under the bus lock
            self.ensure_ready()
            if self.fail_delivered_unknown:
                self.fail_delivered_unknown = False
                self._active = False
                raise HopError("connection lost after send", path=self.host.path,
                               hop="sim-scpi", txn=current_txn.get(),
                               delivered="unknown")
            if self.fail_next > 0:
                self.fail_next -= 1
                self._active = False
                raise HopError("simulated link drop before send",
                               path=self.host.path, hop="sim-scpi",
                               txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                # redact_url: address is ${ENV}-resolved, so its content isn't
                # constrained by the expected label grammar (#126)
                raise HopError(f"no instrument at {redact_url(str(addr))!r}",
                               path=self.host.path,
                               hop="sim-scpi", txn=current_txn.get())
            reply = model.scpi(msg["scpi"])
            self.log.debug("%s %r", "query" if msg.get("query") else "write",
                           msg["scpi"], event="exchange", addr=str(addr))
            return {"reply": reply}


from .. import registry  # noqa: E402

registry.register(SimScpiBus)
