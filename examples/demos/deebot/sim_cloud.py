"""playground,sim-cloud — in-memory stand-in for the Ecovacs portal.

Same exchange() contract and response shape as ecovacs,cloud, so the deebot
driver runs unmodified with zero hardware, credentials, or network
(DESIGN V2: "test before you touch the real motor").
"""
from __future__ import annotations

import logging
from typing import Any, Mapping

from shal import Driver, HopError, MessageTransport, Transport, register
from shal.log import bus_logger, current_txn
from shal.node import Node

logger = logging.getLogger("shal.bus.sim_cloud")


class SimDeebotModel:
    """Tiny state machine mirroring the JSON command set the driver speaks."""

    def __init__(self) -> None:
        self.battery = 87
        self.state = "idle"
        self.docked = True

    def handle(self, cmd: str, data: Mapping) -> tuple[int, dict]:
        if cmd == "getBattery":
            return 0, {"value": self.battery, "isLow": int(self.battery < 15)}
        if cmd in ("getCleanInfo", "getCleanInfo_V2"):
            return 0, {"trigger": "app", "state": self.state}
        if cmd in ("clean", "clean_V2"):
            act = data.get("act")
            if act == "start":
                self.state, self.docked = "clean", False
            elif act == "pause":
                self.state = "pause"
            elif act == "resume":
                self.state = "clean"
            elif act == "stop":
                self.state = "idle"
            else:
                return 1, {}
            return 0, {}
        if cmd == "charge":
            if self.docked:
                return 30007, {}  # already charging — the real robot's answer
            self.state, self.docked = "goCharging", True
            return 0, {}
        if cmd == "playSound":
            return 0, {}
        return 1, {}  # unknown command


@register
class SimEcovacsCloud(Driver, Transport, MessageTransport):
    compatible = "playground,sim-cloud"
    kind = None

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[str, SimDeebotModel] = {}
        self.log = bus_logger("sim_cloud", node.path)

    def activate(self) -> None:
        for n in self.host.walk():
            if n is self.host:
                continue
            comp = getattr(n, "spec", {}).get("driver", "")
            if comp.startswith("ecovacs,deebot"):
                self._models.setdefault(str(n.address), SimDeebotModel())
        super().activate()
        logger.debug("sim cloud activated: %d robot(s)", len(self._models),
                     extra={"path": self.host.path, "txn": current_txn.get()})

    def model_for(self, addr: str) -> SimDeebotModel:
        self.ensure_ready()
        return self._models[str(addr)]

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:
            self.ensure_ready()
            model = self._models.get(str(addr))
            if model is None:
                raise HopError(f"no robot matching {addr!r} on this account",
                               path=self.host.path, hop="sim-cloud",
                               txn=current_txn.get(), delivered="no")
            code, data = model.handle(msg["cmd"], msg.get("data") or {})
            body: dict[str, Any] = {"code": code,
                                    "msg": "ok" if code in (0, 30007) else "fail"}
            if data:
                body["data"] = data
            return {"ret": "ok", "resp": {"header": {"pri": "1"}, "body": body}}
