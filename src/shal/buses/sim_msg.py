"""shal,sim-msg — simulated message service estate (issue #10).

The generic scripted MessageTransport: device/service models are built from the
children's ``compatible`` at activation and answer ``exchange(addr, msg) ->
Mapping`` directly — the sim twin for ANY MessageTransport-kind driver
(HTTP services, cloud devices, JSON-speaking instruments). The demo
``playground,sim-cloud`` (examples/demos/deebot) is the special case this generalizes.

A model is a class registered with ``@msg_sim_model("vendor,part")`` exposing
``handle(msg: Mapping) -> Mapping``. One instance per child node, keyed by the
child's address.

It answers the same two message shapes as ``shal,http`` (issue #104): a plain
mapping reaches the model as-is and its reply comes back as-is; a request
envelope (any of ``method``/``path``/``query``/``headers``/``json``) is
validated and normalised exactly as the wire bus does it (``None`` query params
dropped, ``json`` on GET a LoadError) before the model sees it, and the reply is
``{status, headers, json | text}``. A model answers an envelope with a plain
body (sent as ``200`` + ``json``) or with that full shape; a non-2xx ``status``
is a HopError naming it, as on the wire.
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
from .http_bus import is_envelope, parse_envelope
from .sim_fault import SimFaultMixin

logger = logging.getLogger("shal.bus.sim_msg")

MSG_SIM_MODELS: dict[str, type] = {}


def msg_sim_model(compatible: str):
    """Register a message-service model for ``shal,sim-msg``. The model class
    needs one method: ``handle(msg) -> Mapping``."""
    def deco(cls):
        MSG_SIM_MODELS[compatible] = cls
        return cls
    return deco


class SimMsgBus(SimFaultMixin, Driver, Transport, MessageTransport):
    """A node that provides MessageTransport to its children — entirely in memory."""

    compatible = "shal,sim-msg"
    kind = None
    simulated = True  # issue #347 round 2: a real driver on this bus is still a sim

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[Any, Any] = {}
        self._unmodeled: dict[Any, str] = {}   # declared children with no sim model
        self._init_fault()   # fault: unplugged / SHAL_SIM_UNPLUG / after: N (#304, #349)
        self.fail_next: int = 0          # test hook: fail N next txns (delivered=no)
        self.fail_delivered_unknown = False  # test hook: ambiguous failure
        self.connect_count = 0
        self.log = bus_logger("sim_msg", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, (str, int)) or str(addr) == "":
            # redact_url: child address is ${ENV}-resolved, so its content
            # isn't constrained by the expected label grammar (#126)
            raise LoadError(f"sim-msg: child address must be a non-empty "
                            f"service/device label, got {redact_url(str(addr))!r}")

    def activate(self) -> None:
        self.connect_count += 1
        for node in self.host.walk():
            if node is self.host:
                continue
            comp = getattr(node, "spec", {}).get("driver")
            model = MSG_SIM_MODELS.get(comp)
            if node.address is not None:
                if model is not None:
                    self._models.setdefault(node.address, model())
                elif isinstance(comp, str):
                    self._unmodeled.setdefault(node.address, comp)
                self._register_fault(node)
        super().activate()
        self.log.debug("connect (%d service models)", len(self._models),
                       event="connect")

    def model_for(self, addr: Any):
        self.ensure_ready()
        try:
            return self._models[addr]
        except KeyError:
            have = ", ".join(repr(redact_url(str(a))) for a in self._models) or "none"
            raise LookupError(f"sim-msg: no sim model at {redact_url(str(addr))!r}; "
                              f"addresses with models: {have}") from None

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
            if self._faulted(addr):  # fault: unplugged / SHAL_SIM_UNPLUG (#304, #349)
                # redact_url: address is ${ENV}-resolved (#126)
                raise HopError(f"no service at {redact_url(str(addr))!r}",
                               path=self.host.path, hop="sim-msg",
                               txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                # redact_url: address is ${ENV}-resolved, so its content isn't
                # constrained by the expected label grammar (#126)
                hint = ""
                if addr in self._unmodeled:
                    comp = self._unmodeled[addr]
                    hint = (f" — no sim model registered for {comp!r}; "
                            f"decorate a class with @msg_sim_model({comp!r})")
                raise HopError(f"no service at {redact_url(str(addr))!r}{hint}",
                               path=self.host.path,
                               hop="sim-msg", txn=current_txn.get())
            if not is_envelope(msg):
                reply = model.handle(msg)
                self.log.debug("exchange", event="exchange", addr=str(addr))
                return reply
            envelope = parse_envelope(msg, self.host.path)
            reply = _envelope_reply(model.handle(envelope))
            target = f"{addr}/{envelope['path']}" if envelope["path"] else str(addr)
            # <addr>/<path> and status only — never the query or a header (rule 7)
            self.log.debug("%s %s -> %d", envelope["method"], target,
                           reply["status"], event="exchange", addr=target,
                           status=reply["status"])
            if not 200 <= reply["status"] < 300:
                raise HopError(f"HTTP {reply['status']} from "
                               f"{redact_url(target)}", path=self.host.path,
                               hop="sim-msg", txn=current_txn.get(),
                               delivered="unknown")
            return reply


def _envelope_reply(reply: Any) -> dict:
    """A model's answer to an envelope as ``{status, headers, json | text}``: a
    full-shape reply passes through, anything else is a ``200`` JSON body."""
    if (isinstance(reply, Mapping) and isinstance(reply.get("status"), int)
            and ("json" in reply or "text" in reply)):
        return {"headers": {}, **reply}
    return {"status": 200, "headers": {}, "json": reply}


from .. import registry  # noqa: E402

registry.register(SimMsgBus)
