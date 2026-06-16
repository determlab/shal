# Device card — Ecovacs Deebot vacuum (genuinely UNKNOWN)

> SHAL ships **no** Deebot driver. This device is genuinely unknown to the package.
> You must reach a read **only** via the documented doc→driver path
> (`docs/SDK.md` + the `shal-generate-driver` skill), using the device's own public
> libraries/docs. Do **not** import `shal.drivers.*`, do **not** install any device
> extra, and do **not** use `address: sim` (the read must hit the real robot via the
> cloud).

## What you have

- A real **Ecovacs Deebot** robot vacuum, paired to an Ecovacs account.
- **Cloud-only access.** Unlike Sonos, the Deebot is reached through Ecovacs' **cloud
  API**, not directly on the LAN. Reads go: your code → Ecovacs cloud → robot.
- This means you need **credentials**: an Ecovacs account **email + password** and the
  account **region/country**. If you have not been given these, that is itself a likely
  wall — record it.

## Where to find the device's PUBLIC docs/libraries

- **`deebot-client`** — the maintained Python library for Ecovacs/Deebot cloud control
  (github.com/DeebotUniverse/client.py; `deebot-client` on PyPI). Its docs describe
  authentication and the read commands.
- **`sucks`** — the older library, for reference only.
- The transport is a **proprietary IoT cloud protocol** (Ecovacs auth + MQTT/XMPP-style
  messaging). SHAL does **not** bundle a bus for this transport family, so the doc→driver
  path here likely also needs the **bus** path (`shal-build-bus`) — note that in the EX
  narrative.
- A worked, non-bundled reference of this shape lives at
  `examples/demos/deebot/` (driver + an Ecovacs bus + a sim). It is a **demo**, not the
  shipped package and not a shortcut — you may read it as public reference material the
  same way you'd read any third-party example, but you must still author your own path.

> **CAUTION — the demo is reference, not a shortcut.** The `examples/demos/deebot/`
> driver/bus modules are importable from the working tree and carry their own compatible
> strings and a sim. Do **not** import those modules as if they were part of `pyshal`,
> do **not** reuse the demo's compatible string, and do **not** bind anything to its
> `address: sim`. Author your **own** driver+bus under your **own** compatible
> (e.g. `community,deebot` / `community,ecovacs-cloud`) and read against the **real**
> cloud-paired robot.

## READ-ONLY operations to attempt (never actuate)

Pick one read and make it land. Record the actual returned value **and the address you
read it from** in the report (`read_attempt.raw_response` / `read_attempt.device_address`
— for a cloud device this is the cloud endpoint or the robot's cloud id, never `sim`).

| op (suggested name) | Reads | Notes |
|---------------------|-------|-------|
| `battery` | battery percent (0–100) | "get battery" cloud command |
| `clean_state` | idle / cleaning / returning / charging | "get clean info" cloud command |

**Forbidden (actuation — do not call):** start/stop/pause cleaning, return-to-dock,
relocate, set fan speed, spot/area clean, anything that moves the robot or changes its
state.

## Expected outcome

Two compounding walls are likely, in order:
1. **No driver and no cloud bus** — you must author *both* a driver and a bus from the
   public `deebot-client` docs before any read is possible. This is the full doc→driver
   (+ bus) authoring path end to end.
2. **Credentials** — even with a driver+bus, the read needs Ecovacs account creds and a
   region; the cold start has no documented place to supply them.

This is the device most expected to **find the wall**. Measure exactly how far the cold
agent gets (did the driver author? did the bus author? did auth fail? did the read fire
at all?) and stop honestly at the first one you cannot pass.
