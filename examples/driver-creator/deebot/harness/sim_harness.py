"""Harness-side behavioral sim of the DEEBOT N20 — written from
docs/deebot-protocol.md, deliberately independent of anything a generation
agent produces.

Registered for the bundled ``shal,sim-msg`` bus; the stage-1 harness topology
additionally validates against the untouched golden ``playground,sim-cloud``
bus, whose embedded model speaks the same DN20-PROTO dialect. test_harness.py
force-assigns this class into the sim-msg registry AFTER importing any
generated sim, so validation through sim-msg always hits THIS model.
"""
from __future__ import annotations

from typing import Mapping

from shal.buses.sim_msg import msg_sim_model

_OK_CODES = (0, 30007)


@msg_sim_model("ecovacs,deebot-n20")
class DeebotN20Model:
    """DN20-PROTO state machine (docs §3-§6): bench defaults battery 87,
    state idle, docked."""

    def __init__(self) -> None:
        self.battery = 87
        self.state = "idle"        # idle | clean | pause | goCharging
        self.docked = True

    # -- the sim-msg model interface ------------------------------------
    def handle(self, msg: Mapping) -> Mapping:
        code, data = self._dispatch(msg.get("cmd"), msg.get("data") or {})
        body: dict = {"code": code, "msg": "ok" if code in _OK_CODES else "fail"}
        if data:
            body["data"] = data
        return {"ret": "ok", "resp": {"header": {"pri": "1"}, "body": body}}

    # -- DN20-PROTO semantics --------------------------------------------
    def _dispatch(self, cmd, data: Mapping) -> tuple[int, dict]:
        if cmd == "getBattery":
            return 0, {"value": self.battery, "isLow": int(self.battery < 15)}
        if cmd == "getCleanInfo_V2":
            return 0, {"trigger": "app", "state": self.state}
        if cmd == "clean_V2":
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
            if data.get("act") != "go":
                return 1, {}
            if self.docked:
                return 30007, {}   # already charging: dock request == success
            self.state, self.docked = "goCharging", True
            return 0, {}
        if cmd == "playSound":
            return 0, {}
        return 1, {}               # unknown command: refusal
