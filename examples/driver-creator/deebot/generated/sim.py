"""Behavioral sim model for the ECOVACS DEEBOT N20.

Generated from DN20-PROTO rev 1.2. This is the device's documented behaviour —
the {"cmd","data"} command grammar and the §4 activity state machine — NOT a
copy of the driver's decode math. Setpoints/state read back; commands toggle
state. Registers against the ``shal,sim-msg`` bus (MessageTransport, any
dialect) per SDK §6.

Bench power-on defaults (DN20-PROTO §6): battery value 87 (isLow 0),
state "idle", docked yes (so an immediate charge answers 30007).
"""

from __future__ import annotations

from shal.buses.sim_msg import msg_sim_model

_ALREADY_CHARGING = 30007
_LOW_THRESHOLD = 15


def _ok(data=None):
    """Build a successful DN20-PROTO response envelope.

    ``data`` is omitted entirely when there is nothing to report (§1: the data
    key is present only when the command returns data).
    """
    body = {"code": 0, "msg": "ok"}
    if data is not None:
        body["data"] = data
    return {"ret": "ok", "resp": {"header": {"pri": "1"}, "body": body}}


def _refuse(code: int):
    return {"ret": "ok",
            "resp": {"header": {"pri": "1"},
                     "body": {"code": code, "msg": "fail"}}}


@msg_sim_model("ecovacs,deebot-n20")
class DeebotN20Sim:
    def __init__(self) -> None:
        # DN20-PROTO §6 bench power-on defaults.
        self.battery = 87
        self.state = "idle"
        self.docked = True

    # --- helpers ------------------------------------------------------------

    def _set_state(self, state: str, *, docked: bool) -> None:
        self.state = state
        self.docked = docked

    def _clean(self, data) -> dict:
        act = (data or {}).get("act")
        if act == "start":
            self._set_state("clean", docked=False)
            return _ok()
        if act == "pause":
            self._set_state("pause", docked=False)
            return _ok()
        if act == "resume":
            self._set_state("clean", docked=False)
            return _ok()
        if act == "stop":
            self._set_state("idle", docked=False)
            return _ok()
        return _refuse(1)  # unknown act -> refusal (DN20-PROTO §3.3)

    # --- dispatch -----------------------------------------------------------

    def handle(self, msg):
        cmd = msg.get("cmd")
        data = msg.get("data")

        if cmd == "getBattery":
            return _ok({"value": self.battery,
                        "isLow": 1 if self.battery < _LOW_THRESHOLD else 0})

        if cmd == "getCleanInfo_V2":
            return _ok({"trigger": "app", "state": self.state})

        if cmd == "clean_V2":
            return self._clean(data)

        if cmd == "charge":
            act = (data or {}).get("act")
            if act != "go":
                return _refuse(1)
            if self.docked:
                # 30007 carries msg "ok" (§2) even though it is a non-zero code.
                return {"ret": "ok",
                        "resp": {"header": {"pri": "1"},
                                 "body": {"code": _ALREADY_CHARGING,
                                          "msg": "ok"}}}
            self._set_state("goCharging", docked=False)
            return _ok()

        if cmd == "playSound":
            if (data or {}).get("sid") == 30:
                return _ok()
            return _refuse(1)

        # Unknown command -> refusal with code 1 (DN20-PROTO E6).
        return _refuse(1)
