"""ecovacs,deebot — driver speaking the Ecovacs JSON ("payloadType": "j") command set.

Works against ANY parent bus whose kind is MessageTransport and that accepts
{"cmd", "data"} messages returning portal-shaped responses — the real
ecovacs,cloud bus or the playground sim. The driver never knows which
(DESIGN V2: capability decoupled from how the device is reached).

Model coverage:
  - "ecovacs,deebot"     JSON-protocol models (OZMO 920/950, T5, T8, T9, N8...)
  - "ecovacs,deebot-v2"  models on the *_V2 command set (X1, T10, T20 era)
  - XMPP-era models (pre-~2018, payloadType "x") are NOT supported.

Command names and argument shapes verified against the open-source
deebot-client project (clean/clean_V2, charge {"act": "go"}, getBattery,
getCleanInfo; charge code 30007 == already docked).
"""
from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from shal import Driver, Error, MessageTransport, idempotent, register


@runtime_checkable
class VacuumRobot(Protocol):
    """Blessed-core candidate capability (DESIGN V2 example 3)."""

    def start_cleaning(self) -> None: ...
    def pause(self) -> None: ...
    def resume(self) -> None: ...
    def stop_cleaning(self) -> None: ...
    def dock(self) -> None: ...
    def get_battery_percent(self) -> int: ...
    def get_clean_state(self) -> str: ...


class DeebotError(Error):
    """The robot received the command and answered with a non-ok code.
    Distinct from HopError: delivery is certain, the device refused."""


@register
class Deebot(Driver, VacuumRobot):
    compatible = "ecovacs,deebot"
    kind = MessageTransport

    _CLEAN = "clean"
    _CLEAN_INFO = "getCleanInfo"

    def _clean_args(self, act: str) -> dict:
        return {"act": act, "type": "auto"} if act == "start" else {"act": act}

    # -- capabilities ----------------------------------------------------------

    def start_cleaning(self) -> None:
        self._command(self._CLEAN, self._clean_args("start"))

    def pause(self) -> None:
        self._command(self._CLEAN, self._clean_args("pause"))

    def resume(self) -> None:
        self._command(self._CLEAN, self._clean_args("resume"))

    def stop_cleaning(self) -> None:
        self._command(self._CLEAN, self._clean_args("stop"))

    def dock(self) -> None:
        # 30007 = already charging: docking to the dock you sit on is success
        self._command("charge", {"act": "go"}, ok_codes=(0, 30007))

    def locate(self) -> None:
        """Robot plays the 'I am here' sound."""
        self._command("playSound", {"sid": 30})

    @idempotent
    def get_battery_percent(self) -> int:
        return int(self._command("getBattery")["value"])

    @idempotent
    def get_clean_state(self) -> str:
        """e.g. 'idle' | 'clean' | 'pause' | 'goCharging'."""
        return str(self._command(self._CLEAN_INFO).get("state", "unknown"))

    def send_command(self, cmd: str, data: Optional[dict] = None) -> dict:
        """Escape hatch: raw JSON command (e.g. setSpeed, setWaterInfo).
        NOT idempotent — delivery-unknown failures surface to the caller."""
        return self._command(cmd, data)

    # -- plumbing --------------------------------------------------------------

    def _command(self, cmd: str, data: Optional[dict] = None,
                 ok_codes: tuple[int, ...] = (0,)) -> dict:
        resp = self.bus.exchange(self.addr, {"cmd": cmd, "data": data})
        body = (resp.get("resp") or {}).get("body") or {}
        code = int(body.get("code", 0))
        if code not in ok_codes:
            raise DeebotError(f"{cmd}: robot returned code {code} "
                              f"({body.get('msg', 'no message')})")
        self.log.debug("cmd %s -> code %d", cmd, code)
        out = body.get("data")
        return out if isinstance(out, dict) else {}


@register
class DeebotV2(Deebot):
    compatible = "ecovacs,deebot-v2"

    _CLEAN = "clean_V2"
    _CLEAN_INFO = "getCleanInfo_V2"

    def _clean_args(self, act: str) -> dict:
        if act == "start":
            content: dict = {"type": "auto"}
        elif act == "resume":
            content = {}
        else:
            content = {"type": ""}
        return {"act": act, "content": content}
