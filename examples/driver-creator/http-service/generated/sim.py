"""Behavioral sim model of the Lumen ChamberLink CL-340 controller.

Implements the documented endpoint semantics (docs OpenAPI + notes), NOT a copy
of the driver's math. It maintains chamber state (setpoint, run state, door,
measured temperature) and answers each of the four RPC ops the way the CL-340
firmware does, including the ``{"ok": false, "error": ...}`` refusal shape with
state left unchanged.

Registered against the ``shal,sim-msg`` bus family (MessageTransport, arbitrary
dialect) per the SDK guide §6 table.
"""

from typing import Mapping

from shal.buses.sim_msg import msg_sim_model

# Safe operating envelope from the CL-340 datasheet (docs notes + OpenAPI).
ENVELOPE_MIN = -40.0
ENVELOPE_MAX = 180.0


@msg_sim_model("lumen,chamber-api")
class ChamberLinkSim:
    """One instance per child node; the sim bus builds it at activation."""

    def __init__(self) -> None:
        # Power-on defaults (docs notes "Power-on defaults").
        self.setpoint_c = 22.0
        self.temp_c = 22.0
        self.running = False
        self.door_open = False

    # Test hooks: let tests place the chamber into a documented scenario.
    def _settle(self) -> None:
        """Model the chamber having converged to its setpoint while running."""
        if self.running:
            self.temp_c = self.setpoint_c

    def handle(self, msg: Mapping) -> Mapping:
        op = msg.get("op")

        if op == "get_status":
            return {
                "temp_c": self.temp_c,
                "setpoint_c": self.setpoint_c,
                "door_open": self.door_open,
                "running": self.running,
            }

        if op == "set_temperature":
            celsius = msg.get("celsius")
            if not isinstance(celsius, (int, float)):
                return {"ok": False, "error": "missing or invalid 'celsius'"}
            celsius = float(celsius)
            # Controller's own second-line-of-defense refusal (docs notes).
            # State guaranteed unchanged on refusal.
            if celsius < ENVELOPE_MIN or celsius > ENVELOPE_MAX:
                return {
                    "ok": False,
                    "error": (
                        f"setpoint {celsius:.1f} outside safe envelope "
                        f"[{int(ENVELOPE_MIN)}, {int(ENVELOPE_MAX)}]"
                    ),
                }
            self.setpoint_c = celsius
            return {"ok": True, "setpoint_c": celsius}

        if op == "start":
            # start while the door interlock is open is refused (docs notes).
            if self.door_open:
                return {"ok": False, "error": "door interlock open"}
            # Level-setting: harmless no-op if already running.
            self.running = True
            self._settle()
            return {"ok": True, "running": True}

        if op == "stop":
            # Level-setting: harmless no-op if already stopped.
            self.running = False
            return {"ok": True, "running": False}

        return {"ok": False, "error": f"unknown op {op!r}"}
