"""SHAL driver for the Lumen Instruments ChamberLink CL-340 chamber controller.

Generated from the device documentation only:
  - examples/driver-creator/http-service/docs/chamberlink-openapi.yaml
  - examples/driver-creator/http-service/docs/chamberlink-notes.md

Wire contract (docs OpenAPI + notes): a single RPC endpoint. Every operation is
one JSON object whose "op" field selects one of exactly four operations
(get_status, set_temperature, start, stop). On a SHAL MessageTransport bus the
dict is handed to ``bus.exchange(addr, msg)`` (the ``shal,http`` bus POSTs it to
``<base>/<addr>``; the ``shal,sim-msg`` twin hands it to the model). Replies are
JSON objects; a refused/failed op replies ``{"ok": false, "error": <reason>}``
with the chamber state guaranteed unchanged.
"""

from typing import Mapping

import shal
from shal import Driver, idempotent, op
from shal.transport import MessageTransport


class ChamberError(shal.Error):
    """The chamber refused an operation (transport succeeded, device said no).

    Raised when a reply carries ``{"ok": false, "error": ...}``. Delivery was
    certain, so this must NOT be visible to the retry machinery (SDK guide §5).
    """


@shal.register
class ChamberLinkCL340(Driver, shal.TemperatureSensor):
    compatible = "lumen,chamber-api"
    kind = MessageTransport
    llm_ready = True

    # ---- helpers (underscore-prefixed: not wrapped as capability ops) --------

    def _rpc(self, msg: Mapping) -> Mapping:
        """One RPC round-trip. Surfaces a device refusal as ChamberError.

        Never catches transport errors and never retries — that is the
        framework's job (SDK guide §5).
        """
        reply = self.bus.exchange(self.addr, dict(msg))
        if reply.get("ok") is False:
            raise ChamberError(reply.get("error", "chamber refused operation"))
        return reply

    # ---- TemperatureSensor capability ---------------------------------------

    @idempotent
    @op("Read the chamber's measured air temperature right now. Call to watch "
        "the ramp or confirm the chamber has settled to its setpoint.",
        unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        # Worked example #1: get_status while soaking at 65 degC -> temp_c 65.0
        return float(self._rpc({"op": "get_status"})["temp_c"])

    # ---- local ops -----------------------------------------------------------

    @idempotent
    @op("Read the full live chamber state: measured temperature, active "
        "setpoint, door interlock, and run state. Read-only, safe to poll "
        "(up to 10 Hz). Call to confirm setpoint acceptance or check the door.",
        side_effect="none")
    def read_status(self) -> dict:
        # Worked example #1: -> {temp_c, setpoint_c, door_open, running}
        return dict(self._rpc({"op": "get_status"}))

    @op("Program the chamber's active temperature setpoint (absolute, in "
        "degrees Celsius). The conditioning system must be started for the "
        "chamber to drive the air toward this value.",
        unit="celsius", side_effect="write",
        params={"celsius": {"minimum": -40.0, "maximum": 180.0}})
    def set_temperature(self, celsius: float) -> None:
        # Limits declared above; body stays check-free (framework enforces).
        # Worked example #2: celsius 85.5 -> {"ok": true, "setpoint_c": 85.5}.
        # Not @idempotent: writes are audited and a delivery-unknown setpoint
        # is surfaced to the user, never silently re-fired (SDK guide §5).
        self._rpc({"op": "set_temperature", "celsius": celsius})

    @op("Start conditioning: energize the compressor and heater and drive the "
        "air toward the active setpoint. A physical electromechanical action; "
        "calling while already running is a harmless no-op. Avoid rapid "
        "start/stop cycling (shortens compressor life).",
        side_effect="actuator")
    def start(self) -> None:
        # Worked example #3: start -> {"ok": true, "running": true}.
        # Not @idempotent: a delivery-unknown actuator command must reach the
        # user, not be silently re-fired (SDK guide §5).
        self._rpc({"op": "start"})

    @op("Stop conditioning: de-energize the compressor and heater. The setpoint "
        "is retained; the chamber drifts toward ambient. A physical action; "
        "calling while already stopped is a harmless no-op.",
        side_effect="actuator")
    def stop(self) -> None:
        # Worked example #4: stop -> {"ok": true, "running": false}.
        self._rpc({"op": "stop"})

    # ---- authoring metadata (powers shal.catalog) ----------------------------

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {
                "type": "string",
                "description": (
                    "HTTP service path / sim address the RPC dict is delivered "
                    "to. On shal,http this is the URL path under the base; on "
                    "shal,sim-msg any stable string."
                ),
                "examples": ["chamber"],
            },
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
