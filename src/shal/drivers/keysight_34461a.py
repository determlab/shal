"""keysight,34461a — 6½-digit bench digital multimeter over SCPI
(DigitalMultimeter). One instrument per socket; the node address is a label.
"""
from __future__ import annotations

from .. import registry
from ..capabilities import DigitalMultimeter
from ..driver import Driver, idempotent, op
from ..transport import MessageTransport


@registry.register
class Keysight34461a(Driver, DigitalMultimeter):
    compatible = "keysight,34461a"
    kind = MessageTransport
    llm_ready = True

    def _query(self, cmd: str) -> str:
        return self.bus.exchange(self.addr, {"scpi": cmd, "query": True})["reply"]

    @idempotent
    @op("Measure DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage_dc(self) -> float:
        return float(self._query("MEAS:VOLT:DC?"))

    @idempotent
    @op("Measure DC current now.", unit="ampere", side_effect="none")
    def measure_current_dc(self) -> float:
        return float(self._query("MEAS:CURR:DC?"))

    @idempotent
    @op("Measure resistance now.", unit="ohm", side_effect="none")
    def measure_resistance(self) -> float:
        return float(self._query("MEAS:RES?"))

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "string",
                               "description": "instrument label (one DMM per socket)",
                               "examples": ["dmm"]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
