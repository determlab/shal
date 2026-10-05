# Arena Bench PSU-1 — SCPI command reference

A single-channel programmable DC power supply. Write `driver.py` for it:
`compatible = "arena,bench-psu1"`, bound under a `shal,sim-scpi` bus (same
dialect as `shal,scpi-raw` — your driver runs unchanged against the real
instrument later).

## Commands

| Command | Direction | Effect |
|---|---|---|
| `VOLT <value>` | write | Set the output voltage setpoint, in volts (0-30). |
| `MEAS:VOLT?` | query | Return the measured output voltage, in volts. |
| `OUTP ON` / `OUTP OFF` | write | Enable / disable the output. |

## Suggested ops

- `set_voltage(volts: float) -> None` — `side_effect="actuator"` (energizes
  the output now).
- `measure_voltage() -> float` — `side_effect="none"`.
- `output(on: bool) -> None` — `side_effect="actuator"`.

`shal docs` (in the main `shal` package) is the general guide to writing a
driver; `shal check arena,bench-psu1 --topology <harness topology>` is how
the arena runner checks yours — you won't see that topology file, only
whether your driver passed.
