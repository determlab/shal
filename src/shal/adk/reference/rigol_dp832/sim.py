"""Sim twin of one Rigol DP832 channel, on the ``shal,sim-scpi`` bus.

It answers the same SCPI text the real supply does, so the driver runs unchanged
against it. Import it next to ``driver.py`` (``--drivers``).
"""
import re

from shal.buses.sim_scpi import scpi_sim_model


@scpi_sim_model("rigol,dp832")
class Dp832Model:
    """Behavioural model of one DP832 channel: setpoints echo back on measure,
    output toggles. Enough to exercise every op of the driver."""

    def __init__(self) -> None:
        self.voltage = 0.0
        self.current = 0.0
        self.output_on = False

    _SET_V = re.compile(r"^:?SOUR\d*:VOLT\s+([0-9.eE+-]+)$")
    _SET_I = re.compile(r"^:?SOUR\d*:CURR\s+([0-9.eE+-]+)$")
    _OUTP = re.compile(r"^:?OUTP\s+CH\d+,(ON|OFF)$")

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()
        if m := self._SET_V.match(cmd):
            self.voltage = float(m.group(1))
            return ""
        if m := self._SET_I.match(cmd):
            self.current = float(m.group(1))
            return ""
        if m := self._OUTP.match(cmd):
            self.output_on = m.group(1) == "ON"
            return ""
        if "MEAS:VOLT?" in cmd:
            return f"{self.voltage if self.output_on or True else 0.0:.4f}"
        if "MEAS:CURR?" in cmd:
            return f"{self.current:.4f}"
        if cmd == "*IDN?":
            return "RIGOL TECHNOLOGIES,DP832,SIM000001,00.01.16"
        return ""
