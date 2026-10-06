# Arena Bench Relay-1 — Modbus-framed coil interface

Fictional instrument, no real part — same "Arena Bench" style as `scpi-psu`
and `dmm`, but framed like Modbus (function code + coil address) instead of
SCPI. The framing below is the standard Modbus coil read/write command set,
simplified to one request/reply per call: write `driver.py` for it over
`shal,sim-msg`, which exchanges plain **dicts**, not raw Modbus bytes — no
`pymodbus`, no TCP socket, no byte-level framing to build.

`compatible = "arena,bench-relay1"`, bound under a `shal,sim-msg` bus.

## Channel map

8 relay channels, 0-indexed (`0`-`7`). **Channel 0 is this card's power
relay**: energized (`true`) delivers the card's input to the rest of the
circuit; de-energized (`false`) cuts it — every rail reads 0 V while it is
off. Channels 1-7 exist on the module but are not wired to anything on this
card.

## Commands

### Read Coils — function code `1`

Request: `{"fc": 1, "address": <channel>, "count": <n>}`

Reply: `{"fc": 1, "bits": [<bool>, ...]}` — one boolean per requested
channel, in order starting at `address`; `true` means energized (closed).

### Write Single Coil — function code `5`

Request: `{"fc": 5, "address": <channel>, "value": <bool>}`

Reply: `{"fc": 5, "address": <channel>, "value": <bool>}` — a successful
write echoes the request, same convention real Modbus's Write Single Coil
uses (there, the wire value is `0xFF00`/`0x0000`; here it is just
`true`/`false`).

## Suggested ops

- `read_relay(channel: int) -> bool` — `side_effect="none"`.
- `set_relay(channel: int, on: bool) -> None` — `side_effect="write"`: a
  relay switch is reversible and local to this node, the same class of
  change as any other benign setpoint (it does not need approval the way a
  voltage that could damage the card does).

`shal docs` (in the main `shal` package) is the general guide to writing a
driver; `shal-arena check-driver` is how the arena runner checks yours —
you won't see the harness topology it checks against, only whether your
driver passed.
