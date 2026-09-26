"""rigol,dp832 — programmable triple-output DC power supply over SCPI
(PowerSupply). The node address is the channel number (1-3).
"""
from __future__ import annotations

from .. import registry
from ..capabilities import PowerSupply
from ..driver import Driver, idempotent, op
from ..errors import LoadError
from ..node import Node
from ..transport import MessageTransport


@registry.register
class RigolDp832(Driver, PowerSupply):
    compatible = "rigol,dp832"
    kind = MessageTransport
    llm_ready = True

    def bind(self, node: Node) -> None:
        # parse the channel BEFORE super().bind(): the framework compiles the
        # limit guards there, and op_limits() below needs self.ch to do it
        try:
            self.ch = int(str(node.address).lower().lstrip("ch"))
        except ValueError as e:
            raise LoadError(f"{node.path}: rigol,dp832 address must be a channel "
                            f"number 1-3, got {node.address!r}") from e
        super().bind(node)

    def op_limits(self) -> dict:
        # CH3 is the 5 V rail: 0-5 V (Rigol DP800 Series datasheet, DP832 output
        # ratings). Narrows the 0-30 V family envelope on set_voltage.
        if self.ch == 3:
            return {"set_voltage": {"volts": {"maximum": 5.0}}}
        return {}

    def _write(self, cmd: str) -> None:
        self.bus.exchange(self.addr, {"scpi": cmd})

    def _query(self, cmd: str) -> str:
        return self.bus.exchange(self.addr, {"scpi": cmd, "query": True})["reply"]

    # 0-30 V is the widest DP832 channel rating: CH1 and CH2 are 0-30 V (Rigol
    # DP800 Series datasheet, DP832 output ratings). CH3 (0-5 V) narrows this in
    # op_limits(). Both are checked by the framework before any bus I/O.
    @idempotent  # absolute setpoint: re-asserting the same volts is safe
    @op("Set this channel's output voltage (absolute setpoint).",
        unit="volt", side_effect="write",
        params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> None:
        self._write(f":SOUR{self.ch}:VOLT {volts}")

    @idempotent
    @op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def read_voltage(self) -> float:
        return float(self._query(f":MEAS:VOLT? CH{self.ch}"))

    @idempotent
    @op("Read the measured output current now.", unit="ampere", side_effect="none")
    def read_current(self) -> float:
        return float(self._query(f":MEAS:CURR? CH{self.ch}"))

    @op("Enable or disable this channel's output (energizes hardware).",
        side_effect="actuator")
    def output(self, on: bool) -> None:
        self._write(f":OUTP CH{self.ch},{'ON' if on else 'OFF'}")

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "integer", "minimum": 1, "maximum": 3,
                               "description": "PSU channel", "examples": [1]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
