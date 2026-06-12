# Driver Creator — the generation benchmark (issue #10)

**Claim under test:** given device documentation and the SHAL skills, a coding
agent produces a working driver — and where needed, a bus — with **no human
writing code**.

Every artifact in this tree is one of three kinds:

| Kind | Authored by | Role |
|---|---|---|
| `<case>/docs/` | benchmark (human/infra side) | the device documentation — the ONLY device knowledge the generator gets |
| `<case>/harness/` | benchmark (human/infra side) | the **independent** validation: a behavioral sim written from the same docs + pytest that the generated driver must pass. Never shown to the generator. |
| `<case>/generated/` | a generation agent | the regenerable output: `driver.py`, `sim.py`, `test_*.py`, `topology.yaml`, the registry manifests `device.yaml` + `metadata.yaml`, and `NOTES.md` |

## Registry-convention manifests

Each `generated/` device carries two declarative manifests following the SHAL
Registry artifact conventions (the registry *infrastructure* is out of scope —
only the format applies): **`metadata.yaml`** (a `device://vendor/category/model`
id, provenance, and a `verification.level` — `generated` here, on the
`draft→generated→reviewed→tested→certified` ladder) and **`device.yaml`** (the
declarative definition: capabilities, commands, `safety_constraints` = the
declared limits, and `authentication` credential *requirements* only — never
secrets). They are **emitted** from `shal.catalog(compatible)` so they can't
drift from the driver:

```sh
python examples/driver-creator/emit_manifest.py        # (re)generate all manifests
```

There is deliberately **no hand-written driver catalog here** — drivers exist
only as generation outputs (regenerate them any time; `RECIPE.md` in each case
says how).

## The four cases

1. **`sht31/`** — I²C humidity/temperature sensor from a datasheet excerpt
   (byte-level, declarative, read-only) — over the bundled `shal,sim-i2c`.
2. **`scpi-psu/`** — fictional "Vexar VX3210" programmable PSU from a SCPI
   programming manual — gated writes; **safe operating limits must appear in
   the manifest and be enforced** (`@op params=` → `LimitError` pre-I/O).
3. **`http-service/`** — "Lumen ChamberLink" environmental-chamber REST API
   from an OpenAPI spec — a software node, same model as hardware.
4. **`deebot/`** — robot vacuum from protocol documentation. Stage 1: driver
   only (validated against the untouched golden `playground/deebot/sim_cloud`).
   Stage 2: the **cloud bus too** (`ecovacs_bus.py` hidden) — the full
   "new device on a new transport" contributor journey, validated against a
   local fake portal implementing the documented protocol.

## Rules for generation agents

- Inputs: `<case>/docs/` + [docs/SDK.md](../../docs/SDK.md) +
  `.claude/skills/shal-*` (especially
  [shal-generate-driver](../../.claude/skills/shal-generate-driver/SKILL.md)).
- **Zero reads of `src/shal/**`**, of `playground/deebot/*driver*|*bus*`, of
  other cases, and of `<case>/harness/` — list every file you read in
  `NOTES.md`.
- All trust mechanisms intact: `llm_ready`, `@op` metadata, declared limits for
  every documented range, conformance green.
- A device inexpressible without core changes = STOP and report the design gap.

## Acceptance

```sh
python examples/driver-creator/run_benchmark.py          # all cases, isolated
python examples/driver-creator/run_benchmark.py sht31    # one case
```

The runner executes each case in its **own subprocess** — the harnesses import
modules named `driver`/`sim_harness` off `sys.path`, so a single shared pytest
process would cross-wire them. (A single `pytest examples/driver-creator
--import-mode=importlib` collects cleanly while `generated/` is empty, but the
runner is the canonical gate.)

Per case: the generated tests pass AND the independent harness passes AND
`shal.conformance.check_driver(...)` reports ok. Success criteria from the
issue: ≤1 h human time per device, device-specific driver code <200 lines,
zero SHAL-internals reads, trust mechanisms intact.
