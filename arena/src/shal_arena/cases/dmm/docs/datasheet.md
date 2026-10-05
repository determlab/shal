# Arena Bench DMM-1 — SCPI command reference

A single-input digital multimeter. Write `driver.py` for it:
`compatible = "arena,bench-dmm1"`, bound under a `shal,sim-scpi` bus.

## Commands

| Command | Direction | Effect |
|---|---|---|
| `MEAS:VOLT:DC?` | query | Return the measured DC voltage at the probe, in volts. |

## Suggested ops

- `measure_voltage() -> float` — `side_effect="none"`.

`shal docs` (in the main `shal` package) is the general guide to writing a
driver; `shal check arena,bench-dmm1 --topology <harness topology>` is how
the arena runner checks yours — you won't see that topology file, only
whether your driver passed.
