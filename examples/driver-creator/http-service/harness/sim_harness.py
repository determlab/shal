"""Independent behavioral sim of the Lumen ChamberLink CL-340 controller.

Written from examples/driver-creator/http-service/docs/ ONLY (the OpenAPI
document + API guide). This model is the validation oracle for the
driver-creator benchmark: harness tests force it into the shal,sim-msg
registry so a generated driver is exercised against THIS behavior, not
against the sim the generation agent wrote for itself.

Documented behavior implemented here:
- single RPC dialect: handle({"op": ..., ...}) -> JSON-able dict
- power-on defaults: setpoint_c 22.0, running False, door_open False
  (temp_c tracks the setpoint instantly — the idealized "settled" chamber)
- set_temperature: accepts -40..180 inclusive, echoes the new setpoint;
  refuses anything else with {"ok": False, "error": ...} and leaves all
  state unchanged
- start/stop: level-setting (repeat calls are harmless no-ops)
- any unknown op or malformed body -> ErrorReply, state unchanged
"""
from __future__ import annotations

from shal.buses.sim_msg import msg_sim_model

ENVELOPE_MIN = -40.0
ENVELOPE_MAX = 180.0


@msg_sim_model("lumen,chamber-api")
class ChamberLinkModel:
    """One CL-340 controller. Attributes are plain state — tests poke them."""

    def __init__(self) -> None:
        self.setpoint_c: float = 22.0
        self.temp_c: float = 22.0          # settled chamber: tracks setpoint
        self.running: bool = False
        self.door_open: bool = False

    def handle(self, msg) -> dict:
        if not isinstance(msg, dict):
            return {"ok": False, "error": "body must be a JSON object"}
        op = msg.get("op")

        if op == "get_status":
            return {"temp_c": self.temp_c, "setpoint_c": self.setpoint_c,
                    "door_open": self.door_open, "running": self.running}

        if op == "set_temperature":
            celsius = msg.get("celsius")
            if not isinstance(celsius, (int, float)) or isinstance(celsius, bool):
                return {"ok": False, "error": "missing or non-numeric 'celsius'"}
            if not (ENVELOPE_MIN <= float(celsius) <= ENVELOPE_MAX):
                return {"ok": False,
                        "error": f"setpoint {float(celsius)} outside safe "
                                 f"envelope [-40, 180]"}
            self.setpoint_c = float(celsius)
            self.temp_c = self.setpoint_c   # instant settle
            return {"ok": True, "setpoint_c": self.setpoint_c}

        if op == "start":
            self.running = True
            return {"ok": True, "running": True}

        if op == "stop":
            self.running = False
            return {"ok": True, "running": False}

        return {"ok": False, "error": f"unknown op {op!r}"}
