"""Reference sim for the ``scpi-psu`` ADK case (harness — not given to the
player). Answers the SCPI dialect described in ``docs/datasheet.md``: a
``VOLT <v>`` setpoint echoed back on ``MEAS:VOLT?``, output on/off tracked but
not otherwise enforced — enough to exercise a correctly-written driver, same
shape as the ``rigol_dp832`` ADK reference's ``sim.py``.
"""
from __future__ import annotations

import re

from shal.buses.sim_scpi import scpi_sim_model


@scpi_sim_model("arena,bench-psu1")
class BenchPsu1Model:
    _SET_V = re.compile(r"^VOLT\s+([0-9.eE+-]+)$")
    _OUTP = re.compile(r"^OUTP\s+(ON|OFF)$")

    def __init__(self) -> None:
        self.voltage = 0.0
        self.output_on = False

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()
        if m := self._SET_V.match(cmd):
            self.voltage = float(m.group(1))
            return ""
        if m := self._OUTP.match(cmd):
            self.output_on = m.group(1) == "ON"
            return ""
        if cmd == "MEAS:VOLT?":
            return f"{self.voltage:.6f}"
        return ""
