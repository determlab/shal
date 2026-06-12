"""SHAL driver for the ECOVACS DEEBOT N20 robot vacuum.

Generated from DN20-PROTO rev 1.2 (deebot-protocol.md). The N20 speaks a
transport-agnostic JSON command dialect ``{"cmd": ..., "data": ...}`` that is
identical on every path (cloud relay, LAN bridge, bench emulator), so this
driver is a ``MessageTransport`` consumer: it sends each command via
``self.bus.exchange(self.addr, {"cmd": ..., "data": ...})`` and reads the
documented ``{"ret", "resp": {"body": {"code", "msg", "data"}}}`` envelope back.

Capability: a driver-local ``VacuumRobot`` protocol (no blessed SHAL protocol
fits a robot vacuum — SDK §2 "define a driver-local one" pattern).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import shal
from shal import Driver, idempotent, op


# --- Capability (driver-local; SDK §2 community-protocol pattern) -----------

@runtime_checkable
class VacuumRobot(Protocol):
    """v0.1.0 — a robot vacuum cleaner.

    States are the protocol's activity strings: ``idle``, ``clean``,
    ``pause``, ``goCharging``. Battery is percent state-of-charge (0-100).
    """

    def start_cleaning(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop_cleaning(self) -> None: ...
    def dock(self) -> None: ...
    def locate(self) -> None: ...
    def get_battery_percent(self) -> int: ...
    def get_clean_state(self) -> str: ...


# --- Device error (transport-succeeded-but-robot-refused; SDK §5) -----------

class DeebotError(shal.Error):
    """The robot received a command and refused it (non-zero result ``code``).

    Delivery was certain (the transport returned a ``resp.body``), so this is
    NOT a ``HopError`` and the retry machinery must never see it.
    """

    def __init__(self, cmd: str, code: int, msg: str) -> None:
        super().__init__(f"deebot refused {cmd!r}: code={code} msg={msg!r}")
        self.cmd = cmd
        self.code = code
        self.msg = msg


# Per DN20-PROTO §2: charge returns 30007 when the robot is already docked,
# which is a SUCCESS outcome for a dock request (the end state already holds).
_ALREADY_CHARGING = 30007


@shal.register
class DeebotN20(Driver, VacuumRobot):
    compatible = "ecovacs,deebot-n20"
    kind = shal.MessageTransport
    llm_ready = True

    # --- internal: send one command, return resp.body, surface refusals -----

    def _command(self, cmd: str, data=None, *, ok_codes=(0,)):
        """Send a DN20-PROTO command and return its ``resp.body`` dict.

        Raises ``DeebotError`` on a robot refusal (code not in ``ok_codes``).
        Transport errors propagate untouched (the framework owns retries).
        """
        reply = self.bus.exchange(self.addr, {"cmd": cmd, "data": data})
        body = reply["resp"]["body"]
        code = body["code"]
        if code not in ok_codes:
            raise DeebotError(cmd, code, body.get("msg", "fail"))
        return body

    # --- reads (idempotent; safe to poll/retry) -----------------------------

    @idempotent
    @op("Read the robot's battery state of charge, in percent (0-100). "
        "Call to check whether the robot needs to dock and charge.",
        unit="percent", side_effect="none")
    def get_battery_percent(self) -> int:
        body = self._command("getBattery")
        return int(body["data"]["value"])

    @idempotent
    @op("Read the robot's current activity state: one of 'idle', 'clean', "
        "'pause', 'goCharging'. Call to learn what the robot is doing now.",
        side_effect="none")
    def get_clean_state(self) -> str:
        body = self._command("getCleanInfo_V2")
        return str(body["data"]["state"])

    # --- actuations (NOT idempotent: they change physical state, audited) ----

    @op("Start an automatic whole-area cleaning cycle. The robot leaves the "
        "dock and begins cleaning (state -> 'clean'). Call to begin cleaning.",
        side_effect="actuator")
    def start_cleaning(self) -> None:
        self._command("clean_V2", {"act": "start", "content": {"type": "auto"}})

    @op("Pause the current cleaning cycle in place (state -> 'pause'). "
        "Call to temporarily suspend cleaning; resume() continues it.",
        side_effect="actuator")
    def pause(self) -> None:
        self._command("clean_V2", {"act": "pause", "content": {"type": ""}})

    @op("Resume a paused cleaning cycle (state -> 'clean'). "
        "Call only when the robot is paused.",
        side_effect="actuator")
    def resume(self) -> None:
        self._command("clean_V2", {"act": "resume", "content": {}})

    @op("Abandon the current cleaning cycle (state -> 'idle'). The robot stops "
        "cleaning but does not return to the dock; use dock() for that.",
        side_effect="actuator")
    def stop_cleaning(self) -> None:
        self._command("clean_V2", {"act": "stop", "content": {"type": ""}})

    @op("Send the robot back to its charging dock (state -> 'goCharging'). "
        "If it is already docked this still succeeds. Call to recall the robot.",
        side_effect="actuator")
    def dock(self) -> None:
        # 30007 (already charging) is success for a dock request — DN20-PROTO §2.
        self._command("charge", {"act": "go"}, ok_codes=(0, _ALREADY_CHARGING))

    @op("Play the locate chime ('I am here') on the robot's speaker to find it. "
        "Does not change the activity state.",
        side_effect="actuator")
    def locate(self) -> None:
        self._command("playSound", {"sid": 30})

    # --- authoring surface --------------------------------------------------

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {
                "type": "string",
                "description": "robot device id (did) on the message bus",
                "examples": ["did-bot1"],
            },
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
