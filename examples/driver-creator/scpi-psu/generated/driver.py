"""Vexar Instruments VX3210 — single-output programmable DC power supply.

Generated from docs/vx3210-manual.md (P/N VX3210-PM Rev. C). The VX3210 speaks
a SCPI-raw dialect over a raw TCP socket (port 5025). It exposes one output
channel with a voltage setpoint, a current-limit setpoint and an output-enable
relay, so it implements the blessed ``shal.PowerSupply`` capability plus a
current-limit op.

Commands are sent verbatim (uppercase, no channel prefix). Queries return a
single fixed-point line with three decimal places; writes produce no reply.

SAFETY (manual §3.1): the firmware silently CLAMPS out-of-range setpoints
instead of reporting an error, so client-side rejection of out-of-range values
is mandatory. The ratings table (§3) is declared as ``params=`` limits below;
the framework rejects out-of-range calls before any byte reaches the wire.
"""

import shal
from shal import Driver, idempotent, op


@shal.register
class Vx3210(Driver, shal.PowerSupply):
    compatible = "vexar,vx3210"
    kind = shal.MessageTransport          # parent bus must speak the SCPI-raw dialect
    llm_ready = True

    # -- helpers (underscore-prefixed: not capability ops, not audited) --------

    def _write(self, cmd: str) -> None:
        """Send a SCPI write (no reply expected)."""
        self.bus.exchange(self.addr, {"scpi": cmd})

    def _query(self, cmd: str) -> str:
        """Send a SCPI query and return the single reply line."""
        return self.bus.exchange(self.addr, {"scpi": cmd, "query": True})["reply"]

    # -- capability ops --------------------------------------------------------

    @idempotent  # absolute setpoint re-asserted: safe to run twice
    @op("Program the output voltage setpoint (absolute, in volts). Call before "
        "enabling the output, or to change the regulated voltage. The firmware "
        "silently clamps out-of-range values, so the declared range is enforced "
        "client-side and a rejected call never reaches the instrument.",
        unit="volt", side_effect="write",
        params={"volts": {"minimum": 0.0, "maximum": 32.0}})
    def set_voltage(self, volts: float) -> None:
        self._write(f"VOLT {volts}")

    @idempotent  # absolute setpoint re-asserted: safe to run twice
    @op("Program the current-limit setpoint (absolute, in amperes). This is the "
        "constant-current trip point, not the measured load current. The "
        "firmware silently clamps out-of-range values, so the declared range is "
        "enforced client-side before transmission.",
        unit="ampere", side_effect="write",
        params={"amps": {"minimum": 0.0, "maximum": 5.0}})
    def set_current_limit(self, amps: float) -> None:
        self._write(f"CURR {amps}")

    @idempotent
    @op("Measure the voltage at the output terminals now, in volts. With the "
        "output ON this equals the programmed setpoint; with the output OFF it "
        "reads 0.000. This is the measured output, not the setpoint.",
        unit="volt", side_effect="none")
    def read_voltage(self) -> float:
        return float(self._query("MEAS:VOLT?"))

    @idempotent
    @op("Measure the current drawn by the load now, in amperes. Depends on the "
        "connected load (not the current-limit setpoint); reads 0.000 with the "
        "output OFF.",
        unit="ampere", side_effect="none")
    def read_current(self) -> float:
        return float(self._query("MEAS:CURR?"))

    @op("Enable (True) or disable (False) the output relay, connecting or "
        "disconnecting the output terminals. Disable to make the output safe.",
        side_effect="actuator",
        params={"on": {"type": "boolean", "examples": [False]}})
    def output(self, on: bool) -> None:
        self._write("OUTP ON" if on else "OUTP OFF")

    # -- authoring surface -----------------------------------------------------

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {
                "type": "string",
                "description": "SCPI-raw endpoint: host:port (instrument TCP "
                               "socket, default port 5025).",
                "examples": ["192.168.1.50:5025"],
            },
            "config_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
