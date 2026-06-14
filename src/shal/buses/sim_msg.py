"""shal,sim-msg — simulated message service estate (issue #10).

The generic scripted MessageTransport: device/service models are built from the
children's ``compatible`` at activation and answer ``exchange(addr, msg) ->
Mapping`` directly — the sim twin for ANY MessageTransport-kind driver
(HTTP services, cloud devices, JSON-speaking instruments). The playground's
hand-written ``playground,sim-cloud`` is the special case this generalizes.

A model is a class registered with ``@msg_sim_model("vendor,part")`` exposing
``handle(msg: Mapping) -> Mapping``. One instance per child node, keyed by the
child's address.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..driver import Driver
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn
from ..node import Node
from ..transport import MessageTransport, Transport

logger = logging.getLogger("shal.bus.sim_msg")

MSG_SIM_MODELS: dict[str, type] = {}


def msg_sim_model(compatible: str):
    """Register a message-service model for ``shal,sim-msg``. The model class
    needs one method: ``handle(msg) -> Mapping``."""
    def deco(cls):
        MSG_SIM_MODELS[compatible] = cls
        return cls
    return deco


class SimMsgBus(Driver, Transport, MessageTransport):
    """A node that provides MessageTransport to its children — entirely in memory."""

    compatible = "shal,sim-msg"
    kind = None

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[Any, Any] = {}
        self.fail_next: int = 0          # test hook: fail N next txns (delivered=no)
        self.fail_delivered_unknown = False  # test hook: ambiguous failure
        self.connect_count = 0
        self.log = bus_logger("sim_msg", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, (str, int)) or str(addr) == "":
            raise LoadError(f"sim-msg: child address must be a non-empty "
                            f"service/device label, got {addr!r}")

    def activate(self) -> None:
        self.connect_count += 1
        for node in self.host.walk():
            if node is self.host:
                continue
            comp = getattr(node, "spec", {}).get("driver")
            model = MSG_SIM_MODELS.get(comp)
            if model is not None and node.address is not None:
                self._models.setdefault(node.address, model())
        super().activate()
        self.log.debug("connect (%d service models)", len(self._models),
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
                               hop="sim-msg", txn=current_txn.get(),
                               delivered="unknown")
            if self.fail_next > 0:
                self.fail_next -= 1
                self._active = False
                raise HopError("simulated link drop before send",
                               path=self.host.path, hop="sim-msg",
                               txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                raise HopError(f"no service at {addr!r}", path=self.host.path,
                               hop="sim-msg", txn=current_txn.get())
            reply = model.handle(msg)
            self.log.debug("exchange", event="exchange", addr=str(addr))
            return reply


from .. import registry  # noqa: E402

registry.register(SimMsgBus)
