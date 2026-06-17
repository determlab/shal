# NOTES — Lumen ChamberLink CL-340 driver (compatible `lumen,chamber-api`)

## Files read (the complete input set)

- `examples/driver-creator/http-service/RECIPE.md` — task statement
- `examples/driver-creator/http-service/docs/chamberlink-notes.md` — API guide
- `examples/driver-creator/http-service/docs/chamberlink-openapi.yaml` — OpenAPI 3.0 spec
- `docs/SDK.md` — SHAL authoring contract
- `integrations/claude-code/skills/shal-generate-driver/SKILL.md`
- `integrations/claude-code/skills/shal-build-driver/SKILL.md`
- `integrations/claude-code/skills/shal-build-yaml/SKILL.md`

No SHAL source (`src/shal/**`) and no `harness/**` were read. The only
introspection performed was on the **public** `shal` API surface (attribute
existence, public method names on the loaded hal handle and sim bus) to confirm
names the SDK guide already documents — no internals inspected.

## Device model extracted from the docs

- Identity: Lumen Instruments ChamberLink CL-340 → `compatible = "lumen,chamber-api"`.
- Transport: single-endpoint JSON RPC over HTTP → `kind = MessageTransport`,
  dialect = one JSON dict per `bus.exchange(addr, msg)` (the `shal,http` bus
  POSTs it to `<base>/<addr>`; the `shal,sim-msg` twin hands it to the model).
- Ops (the four documented operations + the blessed read):
  - `read_celsius()` → `TemperatureSensor` capability, from status `temp_c`.
  - `read_status() -> dict` → `get_status`, read-only, idempotent.
  - `set_temperature(celsius)` → `set_temperature`, write, limits −40..180.
  - `start()` / `stop()` → physical actuator ops (compressor + heater).
- Safe envelope: −40 °C..+180 °C (datasheet, encoded as `minimum`/`maximum` on
  `celsius` in the OpenAPI). Declared as `params={"celsius": {minimum, maximum}}`.
  Driver body contains NO range check — the framework enforces the declaration.
- Worked-example vectors (docs notes table + OpenAPI examples) drive the tests:
  #1 get_status 65 °C running; #2 set 85.5 echoes setpoint; #3 start→running;
  #4 stop→not running; #5 set 200 → refused, state unchanged.

## Decisions

- **Capability**: blessed `shal.TemperatureSensor` (`read_celsius`) fits the
  temperature read. `set_temperature`/`start`/`stop`/`read_status` are exposed as
  local ops; no blessed protocol covers an environmental chamber, and the recipe
  specifies these signatures, so no driver-local Protocol was invented (would add
  no callers in this benchmark).
- **Idempotency**:
  - `read_celsius`, `read_status` → `@idempotent` (pure reads; retried once on
    `delivered="no"`).
  - `start`, `stop` → NOT idempotent. Although level-setting (re-asserting is a
    documented harmless no-op), they are real electromechanical events; a
    delivery-unknown command must reach the user, never be silently re-fired
    (SDK §5 retry contract). They carry `side_effect="actuator"`.
  - `set_temperature` → NOT idempotent. An absolute setpoint *could* be marked
    idempotent per SDK §5, BUT it is a `side_effect="write"`, and conformance
    requires write ops to produce an audit record. Empirically (conformance),
    marking it `@idempotent` routed it down the read/retry path and produced no
    audit record. Leaving it unmarked yields the mandated audit trail and the
    correct "delivery-unknown surfaced to the user" behavior. See SDK GAP below.
- **Device-said-no vs transport failure** (SDK §5): the docs' `{"ok": false,
  "error": ...}` refusal means the transport succeeded but the device refused.
  The driver raises a `shal.Error` subclass (`ChamberError`) so the retry
  machinery never sees it. Out-of-range setpoints are caught earlier by the
  declared limits (`shal.LimitError`, pre-I/O) and never reach the device.
- **Sim** (`sim.py`) is behavioral, not an echo of the driver: it keeps chamber
  state (setpoint/run/door/temp), models the controller's own envelope refusal
  with state-unchanged, the door-interlock refusal of `start`, the power-on
  defaults (setpoint 22.0, not running), and convergence of `temp_c` to the
  setpoint while running. The driver's `read_celsius` does no math (the status
  carries Celsius directly), so the "value vector" tests assert the documented
  exchange outputs end-to-end through the sim state machine.

## Doc ambiguities

- The notes say a conforming client MUST refuse out-of-range setpoints *client
  side before transmitting*, AND the controller refuses them as a second line of
  defense. SHAL's declared `params=` limits satisfy the client-side MUST
  (rejected pre-I/O, nothing sent). The sim still implements the controller-side
  refusal for completeness, but in normal operation the limit fires first so the
  device never sees an out-of-range value. Not a conflict — two layers, both
  honored.
- `start` while the door interlock is open is listed as a typical refusal in the
  prose but is not enumerated as a distinct error code in the OpenAPI (which only
  shows the generic ErrorReply). Modeled in the sim as a generic ErrorReply
  ("door interlock open") → surfaces as `ChamberError`. No worked vector exists
  for it, so it is exercised only by my own driver-refusal test, not asserted as
  a fixed string.
- The 30 s minimum compressor restart delay is firmware-internal; the docs say
  clients "need not implement it." Not modeled in the driver or sim (it is not a
  client-side concern and modeling it would add hidden timing state).

## SDK / skill gap (verbatim finding)

The SDK guide (§5) explicitly permits `@idempotent` on "absolute setpoints
re-asserted", and (§7) requires write ops to "actually produce audit records".
For an absolute setpoint write that is BOTH (`set_temperature`), these two
statements pull in opposite directions: marking the op `@idempotent` while it is
`side_effect="write"` caused conformance to report
`set_temperature: write op produced no shal.audit record`. The guide does not
state that `@idempotent` and audited-write are mutually exclusive, nor which to
prefer. Resolution taken: drop `@idempotent` so the mandated audit trail is
produced and the delivery-unknown setpoint is surfaced to the user (the safer
reading of §5). A one-line clarification in §5/§7 — "an audited write op must not
also be `@idempotent`; re-assert at the call site instead" — would remove the
ambiguity. No core change was needed or made.

## Conformance output

```
conformance lumen,chamber-api: OK
  checked  static: capability ops discovered
  checked  static: catalog entry + schemas well-formed
  checked  static: limit declarations reviewed
  checked  live: bound at /bench/chamber on a sim transport (dry-run)
  checked  live: capabilities verified (TemperatureSensor)
  checked  live: limits enforced pre-I/O (2 probe(s))
  checked  live: audit trail present (set_temperature)
```

No warnings (the one numeric write param, `celsius`, has declared bounds).

## driver.py line count

- Total: 121 lines.
- Device-specific code (non-blank, non-comment): 86 lines. Well under the 200
  budget.
```
