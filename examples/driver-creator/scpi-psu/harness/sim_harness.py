"""Harness sim model for the Vexar VX3210 (issue #10 driver-creator benchmark).

Written INDEPENDENTLY from docs/vx3210-manual.md — this is the referee's model
of the instrument, not the generation agent's. The harness test force-installs
this class into the sim-scpi model registry so that a generated driver is
validated against behaviour derived straight from the documentation.

Behaviour implemented (manual sections 3-6):
- VOLT / VOLT?            voltage setpoint, clamped silently to 0.000-32.000 V
- CURR / CURR?            current limit setpoint, clamped silently to 0.000-5.000 A
- MEAS:VOLT?              setpoint when output ON, 0.000 when OFF
- MEAS:CURR?              `load_current` attribute when ON, 0.000 when OFF
- OUTP ON|OFF / OUTP?     output enable, query replies "1"/"0"
- *IDN?                   "VEXAR,VX3210,VX3-24117,1.07"
- queries reply with exactly three decimal places; writes reply ""
- commands are case-sensitive, exactly as printed in the manual
"""
from __future__ import annotations

import re

from shal.buses.sim_scpi import scpi_sim_model

V_MIN, V_MAX = 0.0, 32.0   # manual §3: absolute programmable limits
I_MIN, I_MAX = 0.0, 5.0


@scpi_sim_model("vexar,vx3210")
class Vx3210HarnessModel:
    """One VX3210 unit: serial VX3-24117, firmware 1.07 (manual §1, §5)."""

    IDN = "VEXAR,VX3210,VX3-24117,1.07"

    def __init__(self) -> None:
        # power-on defaults, manual §6
        self.voltage_setpoint = 0.0
        self.current_limit = 0.0
        self.output_on = False
        # test hook: the current the attached (imaginary) load draws when the
        # output is ON — what MEAS:CURR? reports (manual §4.6).
        self.load_current = 0.0

    _VOLT_W = re.compile(r"^VOLT ([+-]?\d+(?:\.\d{1,3})?)$")
    _CURR_W = re.compile(r"^CURR ([+-]?\d+(?:\.\d{1,3})?)$")
    _OUTP_W = re.compile(r"^OUTP (ON|OFF)$")

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()
        if m := self._VOLT_W.match(cmd):
            # manual §3.1: the instrument clamps out-of-range values SILENTLY
            self.voltage_setpoint = min(max(float(m.group(1)), V_MIN), V_MAX)
            return ""
        if m := self._CURR_W.match(cmd):
            self.current_limit = min(max(float(m.group(1)), I_MIN), I_MAX)
            return ""
        if m := self._OUTP_W.match(cmd):
            self.output_on = m.group(1) == "ON"
            return ""
        if cmd == "VOLT?":
            return f"{self.voltage_setpoint:.3f}"
        if cmd == "CURR?":
            return f"{self.current_limit:.3f}"
        if cmd == "MEAS:VOLT?":
            return f"{self.voltage_setpoint if self.output_on else 0.0:.3f}"
        if cmd == "MEAS:CURR?":
            return f"{self.load_current if self.output_on else 0.0:.3f}"
        if cmd == "OUTP?":
            return "1" if self.output_on else "0"
        if cmd == "*IDN?":
            return self.IDN
        return ""  # unrecognised command: the VX3210 stays silent
