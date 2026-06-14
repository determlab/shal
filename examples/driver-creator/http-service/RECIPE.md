# Driver-creator recipe — case 3: HTTP service (Lumen ChamberLink)

**Instruction to the generation agent:** Using ONLY the device documentation in
`examples/driver-creator/http-service/docs/` (an OpenAPI 3.0 spec plus a one-page
API guide), the SHAL SDK guide `docs/SDK.md`, and the `.claude/skills/shal-*`
skills (start with `shal-generate-driver`), generate a working SHAL driver for
the Lumen ChamberLink CL-340 chamber controller and write the standardized
deliverables into `examples/driver-creator/http-service/generated/`:
`driver.py`, `sim.py`, `test_chamber.py` (any `test_*.py` name), `topology.yaml`,
and `NOTES.md`. The driver binds `compatible = "lumen,chamber-api"` with
`kind = MessageTransport`; the wire dialect is a single JSON RPC message dict
passed to `bus.exchange(addr, msg)` (the `shal,http` bus POSTs it to
`<base>/<addr>`; the `shal,sim-msg` twin hands it to the model). It must
implement `shal.TemperatureSensor` (`read_celsius()` from the status
`temp_c`) plus local ops `set_temperature(celsius: float)` (a write with the
documented safe envelope -40..180 declared as `params=` limits), `start()`,
`stop()`, and `read_status() -> dict` (the live status read). Do not read any
SHAL source code and do not look inside `harness/`. Acceptance gate (run from
the repo root, must be fully green, no skips):

```
python -m pytest examples/driver-creator/http-service/harness -q
```

The harness validates the generated driver against an independently written
sim of the documented device behavior, asserts the docs' worked-example
vectors, proves the declared limits reject out-of-range setpoints before any
I/O, and runs `shal.conformance.check_driver("lumen,chamber-api", ...)`.
The generated artifacts' own tests should also pass:
`python -m pytest examples/driver-creator/http-service/generated -q`.
