# NOTES — Vexar VX3210 SHAL driver (driver-creator benchmark, case 2)

## Files read (the complete list)

- `examples/driver-creator/scpi-psu/RECIPE.md` — task statement.
- `examples/driver-creator/scpi-psu/docs/vx3210-manual.md` — the single source of truth.
- `docs/SDK.md` — the authoring contract.
- `.claude/skills/shal-generate-driver/SKILL.md`
- `.claude/skills/shal-build-driver/SKILL.md`
- `.claude/skills/shal-build-yaml/SKILL.md`

I did NOT read `src/shal/**`, the harness, any other case folder, or
`playground/**`. (I introspected public objects with `dir()` at runtime to find
the Hal/bus API — see "SDK gap" below — but read no SHAL source.)

## Device model extracted (manual §1–§6)

- **Identity**: `compatible = "vexar,vx3210"` (`*IDN?` -> `VEXAR,VX3210,...`).
- **Transport**: SCPI raw over TCP 5025 -> `kind = MessageTransport`, scpi-raw
  dialect (`{"scpi": cmd, "query": bool}` -> `{"reply": str}`). Sim bus
  `shal,sim-scpi`, model registered with `@scpi_sim_model`.
- **Capability**: blessed `shal.PowerSupply` fits exactly —
  `set_voltage(volts)`, `read_voltage()` (= `MEAS:VOLT?`, measured output, not
  setpoint), `read_current()` (= `MEAS:CURR?`, measured load current),
  `output(on)`. Plus the required `set_current_limit(amps)` op.
- **Ops / wire**: `VOLT <v>` / `CURR <a>` writes (no reply); `MEAS:VOLT?` /
  `MEAS:CURR?` queries; `OUTP ON|OFF` write; queries return fixed-point 3dp.
- **Ratings (§3) -> `params=` limits**: VOLT 0.000–32.000 V; CURR 0.000–5.000 A.
  Declared on `set_voltage` (`volts` 0..32) and `set_current_limit` (`amps`
  0..5). **No range checks in the method bodies** — the framework enforces the
  declaration (verified: 4 limit-rejection tests + conformance "limits enforced
  pre-I/O").
- **Worked vectors (§5/§6)**: output OFF -> `MEAS:VOLT? 0.000`,
  `MEAS:CURR? 0.000`; output ON @ setpoint 12.500 V, load 842 mA ->
  `MEAS:VOLT? 12.500`, `MEAS:CURR? 0.842`. Used directly as test vectors.

## Decisions

- **No channel prefix.** Single-output instrument; commands are sent verbatim
  (`VOLT 3.300`, not `:SOUR1:VOLT`). The node `address` is the TCP endpoint
  `host:port`, validated by the parent SCPI-raw bus.
- **`output(on)` is NOT `@idempotent`** and `side_effect="actuator"`. SDK §5
  says output toggles must not be marked idempotent (a delivery-unknown toggle
  must reach the user). Empirically this also matters for the trust surface:
  with `@idempotent` on `output`, conformance reported
  `output: write op produced no shal.audit record` — i.e. the idempotent
  fast-path suppressed the actuator's audit record. Removing `@idempotent`
  produced the audit trail and matches the SDK rule, so the safety-correct
  choice and the conformance-correct choice coincide. The two setpoint writes
  ARE `@idempotent` (absolute setpoints, safe to re-assert) and the reads are
  `@idempotent` (measurements).
- **Silent-clamp safety (§3.1).** The firmware clamps out-of-range setpoints
  silently, so client-side rejection is mandatory — exactly what the declared
  `params=` limits give us (reject before any byte is sent). The sim model also
  clamps, so a test that bypassed the driver would still see clamping; the
  driver's declared limits prevent the bypass in the first place.
- **`output` param example.** Added `params={"on": {"type": "boolean",
  "examples": [False]}}` so the conformance audit probe can synthesize a safe
  argument (`False` = output off) when exercising the write.

## Doc ambiguities

- The manual gives no explicit address grammar for the controlling software
  (only "raw TCP socket, port 5025"). I modeled `address` as `host:port` per the
  SCPI-raw dialect convention in SDK §3; `authoring_meta.address_schema`
  documents this. The sim topology uses `192.168.1.50:5025`.
- `*IDN?` is documented but is not part of `PowerSupply` and not required by the
  RECIPE, so it is not exposed as a capability op (it would be an unbounded,
  metadata-only op). The sim still answers it. No driver op needed.

## SDK / skill gap (finding)

- **The Hal runtime API is not documented in `docs/SDK.md`.** The SDK and skills
  show driver/bus authoring and `shal.load(...)` + `conformance.check_driver`,
  and SDK §6 references `bus.model_for(addr)`, `bus.fail_next`,
  `bus.fail_delivered_unknown` — but nothing states how to get a device from a
  loaded topology (the `hal.get_device(id)` method), nor whether activation is
  explicit or lazy. I initially wrote `hal.activate()` (a reasonable guess) and
  it failed with `AttributeError`. I resolved it WITHOUT reading `src/shal`, by
  calling `dir()` on the public `Hal` object: the surface is
  `get_device`, `get_node`, `close`, `tool_schemas`, `tool_catalog`,
  `call_tool` — activation is lazy (no `activate()`; the bus connects on first
  use). Recommend SDK adds a short "Using a loaded topology" section listing
  `hal.get_device(id)` and the lazy-activation contract, since every test file a
  generated driver ships needs it.

## Self-validation

- `python -m pytest examples/driver-creator/scpi-psu/generated -q` -> **12 passed**.
- `conformance.check_driver("vexar,vx3210", topology=topology.yaml)` -> **ok=True**
  (capabilities verified incl. PowerSupply; limits enforced pre-I/O on 4 probes;
  audit trail present for `output`; all schemas well-formed).
- **driver.py device-specific line count**: 99 total lines / 82 non-blank /
  79 non-blank-non-comment — well under the 200-line budget.
