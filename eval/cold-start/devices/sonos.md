# Device card — Sonos speaker (treat as UNKNOWN)

> SHAL **ships** a Sonos driver (`sonos,speaker`). For this test it does **not exist**.
> You must **not** import `shal.drivers.sonos`, **not** reference the `sonos,speaker`
> compatible string in a topology, **not** install `pyshal[sonos]`, **not** run
> `shal-mcp --device sonos`, and **not** use `address: sim`. Approach the speaker as a
> device SHAL has never seen and reach a read **only** via the documented doc→driver
> path, against the speaker's **real IP**.

## What you have

- A real **Sonos speaker** on the local network (same LAN as the eval machine).
- Its IP address (ask the human running the gate if you need it; you may also discover
  it yourself from the speaker's own public protocol — see below).
- **No account / no cloud / no credentials.** Sonos is local-only: control happens on
  the LAN over UPnP/SOAP. This is a friendlier case than Deebot precisely because
  there is no credential wall.

## Where to find the device's PUBLIC docs/libraries

You are allowed to use the device's own public material:

- **SoCo** — the community Python library for Sonos (`soco` on PyPI;
  github.com/SoCo/SoCo). Its docs describe the read calls you need. (Note: `soco` is the
  optional `pyshal[sonos]` runtime dependency *and* a public library in its own right.
  You may read SoCo's public docs; you must **not** install it via the `pyshal[sonos]`
  extra. If you need SoCo to author your own driver, that is itself a finding to record:
  the cold start ships without it.)
- **Sonos UPnP / SOAP control** — the underlying protocol SoCo wraps (the
  `AVTransport` and `RenderingControl` UPnP services). Useful if you author a transport
  by hand instead of leaning on `soco`.

Reaching the speaker is an **HTTP/SOAP over TCP** transport — a transport family SHAL
*does* bundle (`shal,http` / `shal,tcp`), so in principle you need a **driver only**,
not a new bus.

> **CAUTION — do not stumble into the bundled driver.** The compatible string
> `sonos,speaker` resolves to SHAL's **bundled** Sonos driver even on a core-only
> install (the package registers it regardless of extras), and that driver has a
> built-in simulator selected by `address: sim`. Using either is the forbidden
> shortcut, and the scaffolded venv's guard shim makes both **unresolvable**
> (`LoadError: no driver installed`). Author your **own** driver under a **different**
> compatible (e.g. `community,sonos`) and bind it to the speaker's **real IP**, never
> `sim`. Record in the report whether the bundled HTTP/TCP transport was reachable from
> the public SDK surface, or whether you hit a wall there.

## READ-ONLY operations to attempt (never actuate)

Pick one read and make it land. Record the actual returned value **and the real IP you
read it from** in the report (`read_attempt.raw_response` / `read_attempt.device_address`).

| op (suggested name) | Reads | Notes |
|---------------------|-------|-------|
| `now_playing` | current track title / artist | from the transport's "get current track info" |
| `transport_state` | PLAYING / PAUSED / STOPPED | "get current transport info" |
| `get_volume` | 0–100 integer | render-control "get volume" |

**Forbidden (actuation — do not call):** play, pause, stop, next/previous,
set volume, set mute, group/ungroup, anything that changes speaker state.

## Expected outcome

Sonos is local with no credentials, so the *device-access* wall is low. The likely
wall is in the **doc→driver authoring path** itself: can a cold agent, using only
`docs/SDK.md` + the `shal-generate-driver` skill + SoCo's public docs, produce a driver
(under its own compatible) that loads (`shal.load`) and reads through a bundled
transport against the real speaker, without ever touching the shipped `sonos` driver or
a simulator? Measure how far you get and where it stops.
