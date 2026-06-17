# SHT31-DIS driver — generation notes

## Files read (the only files opened)

- `examples/driver-creator/sht31/RECIPE.md` — task statement
- `examples/driver-creator/sht31/docs/sht31-datasheet.md` — device documentation
- `docs/SDK.md` — authoring contract
- `integrations/claude-code/skills/shal-generate-driver/SKILL.md` — recipe
- `integrations/claude-code/skills/shal-build-driver/SKILL.md` — driver contract detail
- `integrations/claude-code/skills/shal-build-yaml/SKILL.md` — topology load API (`shal.load`, `hal.get_device`)

Did NOT read: `src/shal/**`, `examples/driver-creator/sht31/harness/**`, any other
case folder, `playground/**`.

## Device model extracted (datasheet)

- Identity: Sensirion SHT31-DIS → `compatible = "sensirion,sht31"`.
- Transport: I2C register/command device → `kind = ByteTransport`, sim bus `shal,sim-i2c`.
- Ops: one single-shot, high-repeatability, clock-stretching measurement
  (command `0x2C 0x06`) returns a 6-byte frame: `T_MSB T_LSB T_CRC RH_MSB RH_LSB RH_CRC`.
  Both quantities are equal-rank, so two ops:
  - `read_celsius()` → blessed `shal.TemperatureSensor`. `T = -45 + 175*S_T/65535`.
  - `read_humidity_percent()` → driver-local `HumiditySensor` protocol. `RH = 100*S_RH/65535`.
  Each op issues its own fresh single-shot conversion; both are `@idempotent` (pure reads).
- Limits: **none**. Datasheet section 7 states explicitly the device is
  measurement-only with no host-settable operating parameters → no `params=`
  limits declared. The −40…+125 °C / 0…100 %RH figures are the measurement
  *range* of read-back values, not bounds on a settable write parameter, so they
  are not declarable limits (and there is no settable op to attach them to).
- Worked examples (section 6) used as test vectors:
  - S_T 0x6666 → 25.0 °C; S_T 0x851E → 45.99946593423361 °C
  - S_RH 0x8000 → 50.000762951094835 %RH; S_RH 0x3333 → 20.0 %RH
  - complete frame 0x66 0x66 0x93 / 0x80 0x00 0xA2

## Decisions

- **Driver-local capability.** No blessed humidity protocol exists, so defined a
  `@runtime_checkable HumiditySensor` Protocol with `read_humidity_percent() -> float`
  in `driver.py` (SDK §2 pattern), implemented alongside blessed `TemperatureSensor`.
- **One transaction per op.** Rather than caching a shared frame (forbidden: per-node
  instances share class state; and stale state is a hazard), each op runs its own
  single-shot measurement and decodes the field it needs. Keeps ops independent
  and each individually idempotent/retry-safe.
- **Sim is behavioral.** `sim.py` holds raw `S_T`/`S_RH` words as device state,
  implements the `0x2C 0x06` command → 6-byte frame grammar, and computes CRC-8
  (poly 0x31, init 0xFF, no reflect, no final XOR) independently — it does NOT
  reuse the driver's decode math. Default state = the datasheet's complete-frame
  example. Tests inject vectors via `model.s_t` / `model.s_rh`.
- **CRC.** Datasheet says CRC verification is optional for basic operation; the
  driver uses bytes 0–1 / 3–4 directly (does not verify CRC), matching the
  documented "hosts that skip the check" path. The sim emits correct CRC bytes
  for realism.
- **No LimitError tests.** Read-only device with zero declared bounds → no limit
  tests, consistent with RECIPE ("there are no limit tests — conformance green is
  the trust gate").

## Ambiguities / SDK gaps / design gaps

- **`unit` value for humidity.** SDK §2 lists baked-in units (celsius, volts,
  amperes, watts, ohms) but does not name a relative-humidity unit. Used
  `unit="percent"` for `read_humidity_percent`. Conformance accepts it; flagging
  as a minor doc gap — the SDK's unit vocabulary doesn't cover %RH.
- No SDK or skill insufficiency that blocked generation. No need to read or
  modify `src/shal/**`. No design gap requiring a STOP.

## Self-validation

- `python -m pytest examples/driver-creator/sht31/generated -q` → **10 passed**.
- `conformance.check_driver("sensirion,sht31", topology=...)` → **OK** (`r.ok` True).
  Live capability line reports `TemperatureSensor` (blessed); the driver-local
  `HumiditySensor` is covered by `test_isinstance_humidity_sensor`.

## Driver line count

`driver.py` total: 100 lines (incl. docstring, imports, local capability
Protocol, and `authoring_meta`). Device-specific code is well under 200 lines.
