# Device card — Sonos speaker (the "device you own" path)

| field | value |
|---|---|
| make / model | Sonos · any zone (e.g. "Bar's Room"); wraps the `soco` library |
| reached by | **LAN** (no cloud) — `SONOS_HOST` IP, or `soco.discover()` fallback if blank |
| Python floor | 3.10+ (soco is pure-Python; install with `pip install --only-binary=:all: soco` to avoid an lxml source build) |
| config (`.env`) | `SONOS_HOST` (the speaker's LAN IP; may be left blank to discover) |

## Acceptance (the success oracle)
- **liveness reads:** `get_state`, `get_volume` → live data (must raise on none).
- **actuation:** `play` (or a small `set_volume` nudge) — **benign** (`side_effect="write"`).
- **expected:** the read-back changes (e.g. `get_state` becomes `PLAYING`, or volume changes).
- **gate:** **NOT gated** — benign media writes run free. This is correct, not a bypass;
  Sonos validates the whole flow *except* the approve/deny hero (that's an actuator device's
  job). Confirm the gate is present (`shal_approve`/`shal_deny` in the tool surface) and
  simply does not fire for a `write`.
- **teardown:** restore the original transport state + volume (leave the speaker as found).

## Known gotchas (found in run 1)
- A **blank `SONOS_HOST=  # comment`** used to leak the comment as the value → bad IP; fixed
  in the `.env` parser (a whole-line `#` value now resolves to unset). Leave it truly blank
  to use discovery.
