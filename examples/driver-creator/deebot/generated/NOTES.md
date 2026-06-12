# Deebot N20 — generation notes

Generated under the controlled benchmark: docs + SDK + skills only, no reads of
`src/shal/**`, `harness/**`, or `playground/**`.

## Files read (the complete input set used)

- `examples/driver-creator/deebot/RECIPE.md`
- `.claude/skills/shal-generate-driver/SKILL.md`
- `.claude/skills/shal-build-driver/SKILL.md`
- `.claude/skills/shal-build-bus/SKILL.md`
- `.claude/skills/shal-build-yaml/SKILL.md`
- `docs/SDK.md`
- `examples/driver-creator/deebot/docs/deebot-protocol.md`  (DN20-PROTO rev 1.2)
- `examples/driver-creator/deebot/docs/deebot-cloud-transport.md`  (DN20-CLOUD rev 1.1)

`playground/deebot/README.md` and `sim_cloud.py` were NOT read: the docs are the
contract and were self-sufficient (the cloud doc carries the full §10 wire
vectors; nothing forced a peek at the golden sim's dialect).

The SHAL public API surface used by the bus (`Transport.__init__(self, node)`
setting `self.host`/`self.lock`/`self._active`, `node.spec["config"]` carrying
loader-resolved `${ENV}` values, `Hal.get_device`/`get_node`) was discovered by
**runtime introspection of the `shal` package** (`dir()`, `inspect.signature`),
never by reading source. See "SDK gaps" below — some of this should be in SDK.md.

---

## STAGE 1 — driver

**Result: PASS.** 13 generated tests green; `conformance.check_driver` OK
(capability ops, catalog/schemas, limit review, live bind, audit trail on dock).

- **Capability**: no blessed SHAL protocol fits a robot vacuum, so a driver-local
  `@runtime_checkable VacuumRobot` Protocol is defined in `driver.py` (SDK §2
  community-protocol pattern): `start_cleaning/pause/resume/stop_cleaning/dock/
  locate/get_battery_percent/get_clean_state`.
- **Transport**: `kind = MessageTransport`; every command is
  `self.bus.exchange(self.addr, {"cmd","data"})`; the response `resp.body` is
  unwrapped to `{code,msg,data}`.
- **Idempotency**: `getBattery`/`getCleanInfo_V2` are `@idempotent` reads
  (`side_effect="none"`). `clean_V2`/`charge`/`playSound` are unmarked
  actuators (`side_effect="actuator"`) — they change physical state and must
  reach the user (not the retry machinery) on `delivered="unknown"`.
- **dock() / 30007**: `charge` code `30007` ("already charging") is treated as
  success via `ok_codes=(0, 30007)` — the requested end state already holds
  (DN20-PROTO §2/§3.4).
- **Device refusal**: any other non-zero `code` raises `DeebotError`
  (a `shal.Error` subclass, NOT a `HopError`) — delivery was certain, so the
  retry machinery must never see it (SDK §5).
- **Limits**: DN20-PROTO §5 is explicit that the V2 set has **no numeric
  setpoints** — all writable inputs are enumerated `act` strings and the single
  `sid=30`. So there are no `params=` ranges to declare, and conformance raised
  **no unbounded-numeric-write warnings** (correctly). The enum nature is
  enforced behaviorally by the sim (an undocumented `act` refuses with code 1).
- **Sim** (`sim.py`): behavioral §4 state machine with §6 bench power-on
  defaults (battery 87, state idle, docked yes). It is the device's behaviour,
  not an echo of the driver's unwrap math. Note the §1 rule that a success body
  **omits** the `data` key when there is nothing to report — implemented in
  `_ok()`. The `30007` body carries `msg:"ok"` despite the non-zero code (§2).

## STAGE 2 — cloud bus

**Result: PASS.** 8 generated tests green, including a full
driver→bus→**real HTTP**→fake portal→robot round trip and the §10 W1–W3
signature vectors locked to their published digests.

- **`compatible = "ecovacs,cloud-n20"`**, a `Driver + Transport +
  MessageTransport` leaf bus. `kind = None`: it speaks HTTPS itself (stdlib
  `urllib`), so there is no upstream SHAL hop.
- **Node address = two-letter country code** (validated in `__init__`,
  `LoadError` on anything not `[a-z]{2}`); continent derived per §2 with a
  config/`ECOVACS_CONTINENT` override.
- **Auth chain** lazy in `activate()`: (1) main-API `user/login` with
  `MD5(password)` and the §3 signed set; (2) open-API `getAuthCode`; (3) portal
  `loginByItToken` → portal uid/token → §6 auth dict. Then `GetDeviceList`
  (with the `GetGlobalDeviceList` fallback) is cached on the session.
- **Signing** (`_sign`) reproduces §10 W2/W3 exactly (sort by name, concat
  `name=value` no separators, `MD5(key+body+secret)`), verified in tests.
- **Command relay** maps `{"cmd","data"}` onto the §8 `iot/devmanager.do`
  envelope; **`payload.body` is omitted when `data` is null/absent** (§8); the
  portal `{"ret","resp"}` reply is returned **unchanged** so the driver sees the
  exact DN20-PROTO envelope.
- **Credentials/secrets**: from `config:` keys `user`/`password` (conventionally
  `${ECOVACS_EMAIL}`/`${ECOVACS_PASSWORD}`), env fallback. Error text names only
  the URL **path**, never the query string (no creds/tokens leak — SDK §9).
- **`portal_url` override** (config key, env `ECOVACS_PORTAL_URL`) replaces all
  three base URLs with one origin (portal base = `{portal_url}/api`) so the
  whole stack can target a test bench over plain `http://` (§9).
- **delivered=**: login/authcode/portal-login refusals and missing creds →
  `delivered="no"` (nothing reached a robot). Post-send failures (HTTP error,
  timeout, bad body, `ret != "ok"`) → `delivered="unknown"` (SDK §5, §8 — never
  blind-retry actuations). Lifecycle: `is_active()` is a cheap local
  `session is not None`; `close()` drops the session so reconnect re-logs-in.
- **Device resolution** matches a child address against
  `did/name/nick/deviceName/sn` (§7).

## Ambiguities / decisions

- **Test filename**: the RECIPE names `test_deebot_n20.py` / `test_cloud_n20.py`
  (the task prompt said `test_deebot.py`). Used the RECIPE names — they are what
  the acceptance harness directory convention implies.
- **`getCleanInfo_V2` state has no declared unit**; left `side_effect="none"`
  with no `unit=` (it's an enum string, not a measured quantity).
- **`charge` from a non-docked robot**: doc says code 0 + state → goCharging;
  from docked, 30007 + unchanged. Both modelled; both covered by tests.

## SDK / skill gaps (honest)

1. **Hal lifecycle is undocumented.** SDK.md shows `hal.get_device(...)` but not
   that there is **no `activate()` on `Hal`** (buses activate lazily on first
   transport use) nor that `get_device(<bus id>)` returns the bus object itself
   (needed to reach the sim hooks `fail_next` / `fail_delivered_unknown` /
   `model_for`). I had to introspect `dir(hal)` to find this. Worth a one-line
   note in SDK §6/§7.
2. **Bus base-class attributes are only described prose-wise.** SDK §9 / the
   build-bus skill say "`Transport.__init__` is the only stateful base" and to
   use `self.lock`/`ensure_ready()`, but never name the concrete attributes the
   base sets (`self.host`, `self.lock`, `self._active`) or that config arrives
   via `node.spec["config"]` (loader-resolved). I confirmed these by
   introspection. Documenting the injected attribute names for buses (parallel
   to the driver table in §1) would remove the need to introspect.
3. **No `Hal`/bus accessor for "the bus node's resolved config"** is shown; I
   read `node.spec.get("config")` directly. That worked but is an inference, not
   a documented contract.

None of these required modifying `src/shal` — the design fits the framework
cleanly. **No STOP-rule design gap was hit.**
