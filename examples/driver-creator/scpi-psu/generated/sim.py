"""Behavioral sim model for the Vexar VX3210 (manual §4–§6).

This is a behavioral model of the *instrument*, not an echo of the driver's
math. It maintains the documented state — a voltage setpoint, a current-limit
setpoint, an output-enable flag, and a load current — and answers SCPI commands
per the command reference and the worked session in §5/§6.

Key documented behaviors reproduced here:
  * Writes produce no reply (return "").
  * Queries return fixed-point with exactly three decimal places (§2.1).
  * MEAS:VOLT? returns the setpoint when the output is ON, else 0.000 (§4.3).
  * MEAS:CURR? returns the load current when ON, else 0.000 (§4.6).
  * The firmware silently CLAMPS out-of-range setpoints to the ratings limits
    (§3.1) — the model clamps too, so a test that bypassed the driver's limits
    would still see clamping (the driver's job is to reject *before* this).
  * Power-on defaults: setpoint 0, current limit 0, output OFF (§6).
"""

from shal.buses.sim_scpi import scpi_sim_model

V_MIN, V_MAX = 0.0, 32.0
I_MIN, I_MAX = 0.0, 5.0


def _fixed(x: float) -> str:
    return f"{x:.3f}"


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


@scpi_sim_model("vexar,vx3210")
class Vx3210Sim:
    SERIAL = "VX3-24117"
    FIRMWARE = "1.07"

    def __init__(self) -> None:
        # power-on defaults (§6)
        self.volt_set = 0.0
        self.curr_set = 0.0
        self.output_on = False
        # test hook: load current drawn when the output is ON (§4.6 example
        # captured 0.842 A); default to the worked-session value.
        self.load_amps = 0.842

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()

        if cmd == "*IDN?":
            return f"VEXAR,VX3210,{self.SERIAL},{self.FIRMWARE}"

        if cmd.startswith("VOLT "):
            self.volt_set = _clamp(float(cmd[5:]), V_MIN, V_MAX)  # silent clamp
            return ""
        if cmd == "VOLT?":
            return _fixed(self.volt_set)

        if cmd.startswith("CURR "):
            self.curr_set = _clamp(float(cmd[5:]), I_MIN, I_MAX)  # silent clamp
            return ""
        if cmd == "CURR?":
            return _fixed(self.curr_set)

        if cmd == "MEAS:VOLT?":
            return _fixed(self.volt_set if self.output_on else 0.0)
        if cmd == "MEAS:CURR?":
            return _fixed(self.load_amps if self.output_on else 0.0)

        if cmd == "OUTP ON":
            self.output_on = True
            return ""
        if cmd == "OUTP OFF":
            self.output_on = False
            return ""
        if cmd == "OUTP?":
            return "1" if self.output_on else "0"

        raise ValueError(f"VX3210 sim: unsupported command {cmd!r}")
