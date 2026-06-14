# ChamberLink CL-340 — API integration guide

Applies to: Lumen Instruments ChamberLink CL-340 environmental test chamber
controller, firmware 1.4.x. The normative interface definition is the OpenAPI
document `chamberlink-openapi.yaml` in this folder; this page is the prose
context an integrator needs around it.

## Network & authentication

The CL-340 ships with its HTTP service on port 8080 and announces itself via
mDNS as `chamberlink.local`. **There is no authentication** — the controller
is intended for an isolated local bench network only. Do not expose it to a
routed network; there is no TLS, no API key, and no rate limiting beyond the
firmware's own command queue.

## The single-endpoint RPC convention

Everything is `POST /chamber` with a JSON body whose `"op"` field selects one
of exactly four operations: `get_status`, `set_temperature`, `start`, `stop`.
The URL never changes; the body does. Replies are JSON objects, always HTTP
200. This keeps PLC-grade clients trivial: one route, one parser.

## Error convention

Any refused or failed operation returns:

```json
{"ok": false, "error": "<human-readable reason>"}
```

with HTTP status 200. Check the `ok` field, not the status code. When an
operation is refused, the chamber state (setpoint, run state) is guaranteed
unchanged. Typical refusals: a setpoint outside the safe envelope, an unknown
`op`, or `start` while the door interlock is open.

## Reads are safe to poll

`get_status` is strictly read-only and has no side effects on the controller.
Poll it as fast as 10 Hz; the firmware serves it from a live state snapshot.
Use it to confirm setpoint acceptance, watch the temperature ramp, and monitor
the door interlock.

## start / stop are physical actions

`start` energizes the conditioning system — the refrigeration compressor and
the resistive heater bank — and the controller begins driving chamber air
toward the active setpoint. `stop` de-energizes both. These are real
electromechanical events: the compressor audibly starts, and rapid
start/stop cycling shortens its life. The firmware enforces a 30 s minimum
compressor restart delay internally; clients need not implement it but should
avoid hammering `start`/`stop`. Both operations are level-setting (calling
`start` while already running is a harmless no-op that still replies
`{"ok": true, "running": true}`).

## The safe operating envelope

The programmable setpoint range is **-40 °C to +180 °C**. This is the chamber
safe operating envelope from the CL-340 hardware datasheet, and it is encoded
in the OpenAPI schema as `minimum`/`maximum` on the `celsius` parameter.
Per the spec, a conforming client MUST refuse out-of-range setpoints on its
own side before transmitting; the controller also refuses them (ErrorReply,
state unchanged) as a second line of defense.

## Worked request/response examples

These exact exchanges were captured from a CL-340 on firmware 1.4.0 and match
the examples embedded in the OpenAPI document. They are suitable as
integration test vectors.

| # | Request body | Reply body |
|---|---|---|
| 1 | `{"op": "get_status"}` (chamber soaking at 65 °C, running, door closed) | `{"temp_c": 65.0, "setpoint_c": 65.0, "door_open": false, "running": true}` |
| 2 | `{"op": "set_temperature", "celsius": 85.5}` | `{"ok": true, "setpoint_c": 85.5}` |
| 3 | `{"op": "start"}` | `{"ok": true, "running": true}` |
| 4 | `{"op": "stop"}` | `{"ok": true, "running": false}` |
| 5 | `{"op": "set_temperature", "celsius": 200}` | `{"ok": false, "error": "setpoint 200.0 outside safe envelope [-40, 180]"}` |

After example 2, a subsequent `get_status` reports `"setpoint_c": 85.5`, and
(once the chamber has settled) `temp_c` converges to the setpoint.

## Power-on defaults

After a power cycle the controller reports `setpoint_c` = 22.0,
`running` = false, `door_open` per the physical interlock, and `temp_c` at
whatever the chamber air actually measures (ambient, if it has been off for a
while).
