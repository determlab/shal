# Regenerating the Deebot case

**Stage 1 — the driver.** You are given exactly three inputs:
`examples/driver-creator/deebot/docs/deebot-protocol.md`,
[docs/SDK.md](../../../docs/SDK.md), and the `.claude/skills/shal-*` skills
(follow `shal-generate-driver`). From them, generate into
`examples/driver-creator/deebot/generated/` the files `driver.py`, `sim.py`,
`test_deebot_n20.py`, `topology.yaml`, and `NOTES.md`. The driver binds
`compatible = "ecovacs,deebot-n20"` with `kind = MessageTransport`, sends the
documented `{"cmd", "data"}` messages via `self.bus.exchange(self.addr, ...)`,
and exposes exactly these capability ops (a vacuum-robot protocol —
define it driver-locally per SDK §2): `start_cleaning()`, `pause()`,
`resume()`, `stop_cleaning()`, `dock()`, `locate()`,
`get_battery_percent() -> int`, `get_clean_state() -> str`. Reads are
`@idempotent`; actuations are not; `charge` code 30007 is success for
`dock()`; any other non-zero `code` raises a driver-defined `shal.Error`
subclass. The sim model registers via `@msg_sim_model("ecovacs,deebot-n20")`
and implements the documented state machine with the documented bench
power-on defaults. Read nothing outside the three inputs (no `src/shal/**`,
no `examples/demos/**`, no `harness/**`).

**Stage 2 — the bus.** Same rules, one more input:
`examples/driver-creator/deebot/docs/deebot-cloud-transport.md` (follow
`shal-build-bus`). Generate `generated/bus.py` (+ tests in
`generated/test_cloud_n20.py` against your own fake far-side): a
`MessageTransport`-providing bus with `compatible = "ecovacs,cloud-n20"`,
node address = the two-letter country code, credentials from `config:` keys
`user`/`password` (conventionally `${ECOVACS_EMAIL}`/`${ECOVACS_PASSWORD}`),
and the documented `portal_url` override (config key, env fallback
`ECOVACS_PORTAL_URL`) for pointing every endpoint at a test portal. It
performs the documented 3-step auth chain lazily in `activate()`, resolves
robots from `GetDeviceList` by did/name/nick/sn, and maps
`exchange(addr, {"cmd", "data"})` onto the `iot/devmanager.do` envelope,
returning the portal response mapping unchanged.

**Acceptance gate (both stages):**

```sh
python -m pytest examples/driver-creator/deebot/harness -q
```

Stage-1 tests skip until `generated/driver.py` exists; stage-2 tests skip
until `generated/bus.py` exists. The harness validates against its own
independent sims (the golden demos sim cloud (examples/demos/deebot/sim_cloud.py), and a local fake portal
over real HTTP) and runs
`shal.conformance.check_driver("ecovacs,deebot-n20", ...)` — all of it must
be green, alongside your own generated tests.
