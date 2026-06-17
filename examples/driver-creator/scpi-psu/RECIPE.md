# Regeneration recipe — Vexar VX3210 (driver-creator benchmark, case 2)

**Instruction to the generation agent:** Using ONLY (a) the device documentation
in `examples/driver-creator/scpi-psu/docs/` (the VX3210 programming manual is
the single source of truth — the instrument is fictional, so no outside
knowledge applies), (b) the authoring contract `docs/SDK.md`, and (c) the
`integrations/claude-code/skills/shal-generate-driver` skill (with the other `shal-*` skills as
linked), generate a complete SHAL driver for the Vexar VX3210 bench power
supply and write the standardized deliverables into
`examples/driver-creator/scpi-psu/generated/`: `driver.py` (compatible
`"vexar,vx3210"`, `kind = MessageTransport`, scpi-raw dialect, `llm_ready`),
`sim.py` (your own behavioral sim model registered with
`@scpi_sim_model("vexar,vx3210")`), `test_vx3210.py` (worked-example vectors,
retry, limit-rejection, capability tests), `topology.yaml` (your sim topology),
and `NOTES.md`. The driver must implement `shal.PowerSupply` exactly
(`set_voltage(volts)`, `read_voltage()` = measured output, `read_current()` =
measured load current, `output(on)`) plus a current-limit op named
`set_current_limit(amps: float) -> None`, and must declare every programmable
limit from the manual's ratings table as `params=` bounds (the manual's safety
note makes client-side rejection mandatory). Do not read SHAL source code and
do not modify anything outside `generated/`.

**Acceptance gate** (run from the repo root; independent harness, your sim
model is overridden by the referee's):

```
python -m pytest examples/driver-creator/scpi-psu/harness -q
```

All harness tests must pass, including `shal.conformance.check_driver` green.
