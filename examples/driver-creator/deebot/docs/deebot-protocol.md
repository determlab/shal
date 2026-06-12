# ECOVACS DEEBOT N20 — Device Command Protocol (JSON dialect, V2 family)

Document DN20-PROTO rev 1.2 — applies to the DEEBOT N20 series robot vacuum
(vendor id `ecovacs`, model id `deebot-n20`).

This document specifies the **transport-agnostic JSON command dialect** spoken
by the robot's command endpoint. It does not matter how a command reaches the
robot (cloud relay, LAN bridge, test bench emulator): the request and response
payloads below are identical on every path. The cloud relay itself is specified
separately in *DN20-CLOUD: Cloud Transport Protocol*
(`deebot-cloud-transport.md`).

## 1. Message envelope

A command is a single JSON object:

```json
{"cmd": "<commandName>", "data": <object or null>}
```

- `cmd` — the command name, case-sensitive (see §3).
- `data` — the command's argument object, or `null` (equivalently absent) for
  commands that take no arguments.

Every command produces a single JSON response:

```json
{
  "ret": "ok",
  "resp": {
    "header": { "pri": "1" },
    "body": {
      "code": 0,
      "msg": "ok",
      "data": { }
    }
  }
}
```

- `ret` — relay-level status. `"ok"` means the response below came from the
  robot. (A non-`"ok"` value is a transport failure, not a robot answer; see
  the transport document.)
- `resp.header` — opaque routing/version metadata. Consumers MUST tolerate
  arbitrary keys here and MUST NOT rely on its contents.
- `resp.body` — the robot's answer. This is the part that matters:

| Field | Type | Meaning |
|---|---|---|
| `code` | int | Result code, see §2 |
| `msg`  | str | `"ok"` for accepted commands, `"fail"` otherwise |
| `data` | object | Command-specific payload. **Present only when the command returns data**; commands with nothing to report omit the key entirely. |

## 2. Result codes

| `code` | Meaning | Handling |
|---|---|---|
| `0` | Command accepted and executed. | Success. |
| `30007` | *Already charging* — returned by `charge` when the robot is already sitting on the dock. | **Treat as success** for a dock request: the requested end state ("robot on dock, charging") already holds. `msg` is `"ok"`. |
| any other non-zero | The robot received the command and **refused** it (unknown command, malformed arguments, state that forbids the action). | Surface as a device error. The robot did receive the message — this is not a delivery failure. `msg` is `"fail"`. |

## 3. Command reference

The N20 speaks the *V2* command family (`payloadType "j"` generation).
Commands fall into two classes:

- **Reads** — `getBattery`, `getCleanInfo_V2`. No effect on the robot; safe to
  poll at any frequency the transport allows. Repeating a read is always safe.
- **Actuations** — `clean_V2`, `charge`, `playSound`. These change the physical
  state of the robot (motion, sound). Blindly re-sending an actuation whose
  delivery outcome is unknown can produce a second physical action.

### 3.1 `getBattery` (read)

Request `data`: `null`.

Response `data`:

| Field | Type | Range | Meaning |
|---|---|---|---|
| `value` | int | 0–100 | Battery state of charge, percent |
| `isLow` | int | 0 or 1 | `1` if and only if `value` < 15 (the low-battery threshold), else `0` |

### 3.2 `getCleanInfo_V2` (read)

Request `data`: `null`.

Response `data`:

| Field | Type | Meaning |
|---|---|---|
| `trigger` | str | What initiated the current activity (e.g. `"app"`). Informational. |
| `state` | str | The robot's activity state — exactly one of `"idle"`, `"clean"`, `"pause"`, `"goCharging"` (see the state machine, §4) |

### 3.3 `clean_V2` (actuation)

Controls the cleaning cycle. Request `data` is
`{"act": <action>, "content": <object>}`; the `content` shape depends on the
action and must be sent exactly as below:

| Action | Request `data` | Effect |
|---|---|---|
| start  | `{"act": "start", "content": {"type": "auto"}}` | Begin an automatic whole-area clean. State → `clean`; the robot leaves the dock. |
| pause  | `{"act": "pause", "content": {"type": ""}}` | Suspend the cycle in place. State → `pause`. |
| resume | `{"act": "resume", "content": {}}` | Continue a paused cycle. State → `clean`. |
| stop   | `{"act": "stop", "content": {"type": ""}}` | Abandon the cycle. State → `idle`. |

Any other `act` value is refused with a non-zero `code`. A successful
`clean_V2` returns `code` 0 and **no** `data` key.

### 3.4 `charge` (actuation)

Request `data`: `{"act": "go"}` — the only defined action.

Sends the robot back to its dock. State → `goCharging`. If the robot is
already docked, the robot answers `code` `30007` (*already charging*) and does
not move; per §2 this is a success outcome for a dock request. A successful
`charge` returns no `data` key.

### 3.5 `playSound` (actuation)

Request `data`: `{"sid": <int>}`.

Plays a voice/chime clip on the robot's speaker. The only sound id defined for
the N20 is **`sid` 30** — the locate chime ("I am here"), used to find the
robot. Does not change the activity state. Returns `code` 0 with no `data`
key.

## 4. Activity state machine

```
            clean_V2 start                  charge (act go)
   idle ───────────────────▶ clean ────────────────────────▶ goCharging
    ▲                        │   ▲                                │
    │ clean_V2 stop          │   │ clean_V2 resume                │ (reaches dock,
    └────────────────────────┤   │                                │  charges; next
                       pause ◀───┘  clean_V2 pause                ▼  cycle starts
                                                          docked / idle)
```

- `charge` is accepted from any state; from a docked robot it yields `30007`.
- `clean_V2 start` from the dock undocks the robot.
- A robot that is on its dock reports `state` per its last activity
  (`goCharging` while returning/charging after a recall, `idle` after power-on).

## 5. Operating limits

The V2 command set above contains **no numeric setpoints**: all writable
inputs are the enumerated `act` strings and the single defined `sid` (30).
Battery percentage (`value`, 0–100) and `isLow` are read-only telemetry.
Implementations MUST send the enumerated values exactly as written; the robot
refuses anything else with a non-zero `code`.

## 6. Bench / emulator power-on defaults

For reproducible integration testing, a conformant N20 protocol emulator
starts in the canonical bench state:

| Quantity | Power-on value |
|---|---|
| Battery `value` | `87` (so `isLow` = 0) |
| Activity `state` | `"idle"` |
| Docked | yes (so an immediate `charge` answers `30007`) |

## 7. Worked examples (exact request/response pairs)

**E1 — battery read at bench defaults:**

```json
→ {"cmd": "getBattery", "data": null}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 0, "msg": "ok", "data": {"value": 87, "isLow": 0}}}}
```

(With the battery at 9 %, the same read returns
`{"value": 9, "isLow": 1}`.)

**E2 — activity state read while idle:**

```json
→ {"cmd": "getCleanInfo_V2", "data": null}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 0, "msg": "ok", "data": {"trigger": "app", "state": "idle"}}}}
```

**E3 — start an auto clean (note: no `data` key in the success body):**

```json
→ {"cmd": "clean_V2", "data": {"act": "start", "content": {"type": "auto"}}}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 0, "msg": "ok"}}}
```

A follow-up `getCleanInfo_V2` now reports `"state": "clean"`.

**E4 — recall to dock while the robot is ALREADY docked (the 30007 success):**

```json
→ {"cmd": "charge", "data": {"act": "go"}}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 30007, "msg": "ok"}}}
```

The robot does not move and its state is unchanged. A dock request must report
this outcome as success. The same request sent while the robot is out cleaning
returns `code` 0 and the state becomes `"goCharging"`.

**E5 — locate chime:**

```json
→ {"cmd": "playSound", "data": {"sid": 30}}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 0, "msg": "ok"}}}
```

**E6 — refusal (unknown command):**

```json
→ {"cmd": "getFooBar", "data": null}
← {"ret": "ok", "resp": {"header": {"pri": "1"},
     "body": {"code": 1, "msg": "fail"}}}
```
