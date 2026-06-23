---
name: shal-generate-driver
description: Generate a working SHAL driver (and its sim model + tests) from device documentation — a datasheet, instrument manual, OpenAPI spec, or protocol doc. Use when asked to add device support given docs, with no human-written code. The recipe reads src/shal/SDK.md and zero SHAL internals.
---

# Generate a SHAL driver from device documentation

You are turning a document into three artifacts: **driver + sim model + tests**,
proven by the conformance kit. Read [src/shal/SDK.md](../../../src/shal/SDK.md) first —
it is the complete authoring contract. **Do not read SHAL source code**; if the
SDK guide is insufficient, that's a finding to report, not a reason to peek.

## Step 0 — Extract the device model from the doc

Read the documentation and write down (as comments or a scratch table):

1. **Identity**: vendor, part → `compatible = "vendor,part"` (lowercase).
2. **Transport**: I²C registers → `kind = ByteTransport`; SCPI commands →
   `MessageTransport` (scpi dialect); REST/JSON → `MessageTransport`.
3. **Ops**: every read/measurement and every command worth exposing. For each:
   the wire exchange (register + bytes / SCPI string / message shape), the
   decode/encode math, the unit.
4. **Absolute maximum ratings / programmable ranges** → these become `params=`
   limits. If the doc states a range for a settable quantity, declaring it is
   MANDATORY, not optional.
5. **Worked examples** (datasheets show example raw→value conversions; manuals
   show example command/response pairs) → these become your test vectors.
   If the doc gives none, derive two by hand from the formulas and show the
   arithmetic in a comment.

## Step 1 — Pick the capability

- A blessed protocol from SDK §2 fits → implement it exactly (names,
  signatures, units).
- Nothing fits → define a driver-local `@runtime_checkable` Protocol in your
  module (SDK §2 pattern). A device may implement both (e.g. SHT31 =
  `TemperatureSensor` + local `HumiditySensor`).

## Step 2 — Write the driver (manifest first)

Order of writing: `compatible`/`kind`/`llm_ready` → `authoring_meta()` → the
`@op` decorators with descriptions, units, side_effects, and **limits from
step 0.4** → only then the method bodies (decode math from the doc's formulas).
Style: SDK §1. Budget: well under 200 lines. The body of a limited op contains
NO range checks — the framework enforces your declaration.

SCPI specifics: `self.bus.exchange(self.addr, {"scpi": cmd, "query": True})
["reply"]` for queries, omit `query` for writes; channel/instrument selection
comes from `self.addr`. HTTP/REST specifics: the message dict you pass to
`exchange(self.addr, msg)` is POSTed to the service path = your node address;
shape it after the API's request schema, read the reply per the response schema.

## Step 3 — Write the sim model from the SAME doc

Register the matching sim model (SDK §6 table). The model implements the
device's documented behavior — register map state machine, SCPI command
grammar, or endpoint semantics — NOT a copy of your driver's math. Setpoints
should read back; state should toggle. This is what makes validation real.

## Step 4 — Write the tests

A `tests/` (or local `test_*.py`) file with a sim topology (SDK §6) covering:

- one value test per op using the **step-0.5 worked examples**;
- retry: `bus.fail_next = 1` → idempotent read still succeeds;
  `bus.fail_delivered_unknown = True` → raises with `delivered == "unknown"`;
- one `LimitError` test per declared bound (call past it; assert the sim model
  state did NOT change);
- `isinstance(dev, <CapabilityProtocol>)`.

## Step 5 — Certify

```python
from shal import conformance
report = conformance.check_driver("vendor,part", topology=<sim yaml>)
assert report.ok, str(report)
```

Run pytest + the conformance check. Iterate until both are green — the errors
name what's missing. Address every conformance WARNING or justify it in a
comment (e.g. a genuinely unbounded parameter).

## Step 6 — Deliverables

```
<case>/generated/
  driver.py        # the generated driver (+ local capability Protocol if any)
  sim.py           # the sim model (separate file from the driver)
  test_<part>.py   # the tests
  topology.yaml    # the sim topology used by tests/conformance
  device.yaml      # registry definition (see Step 7) — emitted, not hand-written
  metadata.yaml    # registry manifest (see Step 7) — emitted, not hand-written
  NOTES.md         # doc sections used, decisions made, anything ambiguous,
                   # and any SDK/skill gap you hit (verbatim, honest)
```

## Step 7 — Registry-convention manifests

Every generated device ships two declarative manifests following the SHAL
Registry conventions (the registry *infrastructure* — search/install/CI — is
out of scope; only the artifact format applies here):

- **`metadata.yaml`** — identity + provenance: a `device://vendor/category/model`
  id, vendor, model, category, protocols, the `compatible` binding id,
  documentation references, and a `verification` block whose `level` is
  **`generated`** (the ladder is `draft → generated → reviewed → tested →
  certified`).
- **`device.yaml`** — the declarative definition DERIVED from the driver:
  capabilities, commands (name/description/side_effect/idempotent/unit/
  parameters), `safety_constraints` (the declared `params=` limits), transport
  requirement, and `authentication` — credential **requirements only**
  (`type`, `required_secrets`), **never secrets**.

Don't hand-write these — they must not drift from the driver. Emit them from
`shal.catalog(compatible)` plus a per-case seed (the `device://` id, category,
display names, doc refs, auth requirements). See
`examples/driver-creator/emit_manifest.py` for the emitter; add a seed entry for
your case and run it. `metadata.yaml` keeps `level: generated` even though the
driver passed conformance + the harness (`harness_validated: true`); the
registry-ladder `tested` level is reserved for registry CI.

## Generating a BUS instead (device on a new transport)

When the device needs a transport no bundled bus covers, follow
[shal-build-bus](../shal-build-bus/SKILL.md) with the protocol documentation as
your wire spec. Same rules: SDK + skills only, no SHAL internals; deliver
`bus.py` + a fake far-side for tests + the same NOTES.md.

## Hard rules

- Zero reads of `src/shal/**` — the SDK guide and skills are the contract.
- Never weaken, skip, or reimplement a trust mechanism (limits, audit, retry,
  llm_ready). If one seems to be in your way, that's a NOTES.md finding.
- Every documented safe range on a settable parameter is declared in `params=`.
- The sim is behavioral (from the doc), not an echo of the driver.
- If the device cannot be expressed without changing SHAL core: STOP and
  report the design gap in NOTES.md. Do not work around it.
