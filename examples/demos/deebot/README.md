# Deebot over SHAL (playground)

Controls an Ecovacs Deebot robot vacuum through SHAL's Phase 1 sync core.
Nothing in `shal/` was modified: the bus and driver register themselves via
`shal.register` (the documented in-process path; a published package would use
the `shal.drivers` entry-point group).

## What's here

| File | Role |
|---|---|
| `ecovacs_bus.py` | `ecovacs,cloud` — a `MessageTransport` bus to the Ecovacs cloud portal (login chain + `iot/devmanager.do` commands) |
| `deebot_driver.py` | `ecovacs,deebot` / `ecovacs,deebot-v2` — the device driver; `VacuumRobot` capability |
| `sim_cloud.py` | `playground,sim-cloud` — in-memory portal stand-in, same response shape |
| `deebot_sim.yaml` / `demo_sim.py` | runs the full stack with zero hardware/credentials — **tested, works** |
| `deebot_real.yaml` / `demo_real.py` | the real robot via the Ecovacs cloud |

The driver is identical on both paths — swap the bus in YAML, nothing else
changes (the core SHAL promise).

## Run the sim (no robot needed)

```
python demo_sim.py
```

## Run against your robot

1. Edit `deebot_real.yaml`: account country code, and the robot's name as it
   appears in the Ecovacs app (its `nick`; the serial number also works).
   For X1/T10/T20-era models change the driver to `ecovacs,deebot-v2`.
2. Credentials are declared on the `ecovacs` node in the YAML:
   ```yaml
   config:
     user: ${ECOVACS_EMAIL}         # resolved from the environment at load
     password: ${ECOVACS_PASSWORD}  # or a literal — only if the file never leaves your machine
   ```
   For the `${VAR}` form, store the values once:
   ```powershell
   [Environment]::SetEnvironmentVariable("ECOVACS_EMAIL", "you@example.com", "User")
   [Environment]::SetEnvironmentVariable("ECOVACS_PASSWORD", "...", "User")
   ```
   Missing credentials fail at load with this exact recipe in the error.
3. Start with the harmless one (robot plays a sound):
   ```
   python demo_real.py --locate
   ```
   Then `--clean`, `--dock`, etc. No flag = read-only battery + state.

Library use:

```python
import shal, deebot_driver, ecovacs_bus

with shal.load("deebot_real.yaml") as hal:
    bot = hal.get_device("cleaner")
    bot.start_cleaning()
    print(bot.get_battery_percent(), bot.get_clean_state())
    bot.dock()
```

`bot.send_command("setSpeed", {"speed": 1})` is the escape hatch for anything
not wrapped yet.

## How it maps to SHAL concepts

- **Bus** — the cloud hop is "just another bus" (DESIGN V2): `ecovacs,cloud`
  implements `MessageTransport.exchange(addr, msg)`. Login is lazy `activate()`;
  `close()` drops the session, so the framework's reconnect-once machinery
  re-logs-in.
- **Driver** — `Deebot` declares `kind = MessageTransport` and is bound by
  `compatible`; it works unchanged behind the sim or the real cloud.
- **Capability** — `VacuumRobot` Protocol; user code depends on it, not the driver.
- **Retry policy** — `get_battery_percent` / `get_clean_state` are
  `@shal.idempotent` (auto-retry across drops). `start_cleaning`, `dock`, etc.
  are writes: a delivery-unknown failure raises `HopError(delivered="unknown")`
  and is **never** auto-retried — you decide (you can see whether the robot moved).
- **Secrets** — credentials only via env; error messages and logs never carry
  query strings or tokens.

## Caveats (read before blaming the robot)

- **The cloud path is written but not live-tested** — I had no Ecovacs account
  or robot during development. The protocol (endpoints, signing keys, payload
  shapes) was verified against the open-source
  [deebot-client](https://github.com/DeebotUniverse/client.py) project, which is
  what Home Assistant uses, but Ecovacs changes nothing-guaranteed cloud APIs.
  If auth fails, run `demo_real.py -v` and compare against deebot-client.
- **Model variance is real.** JSON-protocol models (OZMO 920/950, T5/T8/T9/N8…)
  use `ecovacs,deebot`; X1/T10/T20-era use `ecovacs,deebot-v2` (`clean_V2`).
  Pre-2018 XMPP models are not supported.
- **Region servers**: continent is derived from the country code; override with
  `$env:ECOVACS_CONTINENT` (`eu` / `na` / `as` / `ww`) if login succeeds but the
  portal can't find your robot.
- **Phase 1 = sync command/poll.** Live status push (the `bot.events("status")`
  loop from DESIGN V2 example 3) needs the Phase 2 `Stream` kind over MQTT.
  Polling `get_clean_state()` is the Phase 1 way.
