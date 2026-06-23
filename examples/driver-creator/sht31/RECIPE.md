# Regenerating the SHT31 driver

Hand a generation agent exactly this instruction:

> Using ONLY `examples/driver-creator/sht31/docs/` (the device documentation),
> `src/shal/SDK.md` (the authoring contract), and the `integrations/claude-code/skills/shal-*`
> skills (follow `shal-generate-driver`), generate a working driver for the
> I²C humidity/temperature sensor with `compatible = "sensirion,sht31"`
> (`kind = ByteTransport`, bundled `shal,sim-i2c` sim bus). The driver must
> implement `shal.TemperatureSensor` (`read_celsius() -> float`) and a
> driver-local humidity capability with an op named
> `read_humidity_percent() -> float` — both measurements are equally
> important. Write the deliverables to `examples/driver-creator/sht31/generated/`
> with exactly these names: `driver.py`, `sim.py`, `test_sht31.py` (any
> `test_*.py`), `topology.yaml`, `NOTES.md`. Do not read `src/shal/**`,
> `examples/driver-creator/sht31/harness/`, or any other case. Done means your
> own tests pass, `shal.conformance.check_driver("sensirion,sht31",
> topology=...)` is green, and the independent acceptance gate passes:
> `python -m pytest examples/driver-creator/sht31/harness -q`.

The harness validates against its own behavioral sim (written from the same
datasheet) using the datasheet's worked-example vectors, then asserts the
conformance report is ok. The device is read-only, so there are no limit
tests — conformance green is the trust gate.
