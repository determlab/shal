---
type: readme
owner: repo-agent
scope: repo/shal
reviewed: 2026-06-23
---

# Sonos over SHAL (example)

Controls a Sonos speaker through SHAL by wrapping the [`soco`](https://github.com/SoCo/SoCo)
library. The driver is the ADK reference `sonos` (#152): it ships in the wheel
as guide material at `src/shal/adk/reference/sonos/` (`shal docs --example sonos`
prints it), and `sonos_driver.py` here only imports it, so there is one copy. It
registers itself via `@registry.register` when imported (the documented
out-of-tree path; a published device package would use the `shal.drivers`
entry-point group instead).

This is a **"wrap an existing Python library" driver** — the smallest kind of
SHAL driver: a root driver (`kind = None`, no SHAL bus) that calls a third-party
library directly. Use it as a template for any device that already has a Python
library.

## What's here

| File | Role |
|---|---|
| `sonos_driver.py` | imports `sonos,speaker` from the ADK reference (`shal.adk.reference.sonos`); implements the `MediaPlayer` capability. Sim-first (`address: sim` needs no `soco`). |
| `sonos_sim.yaml` / `demo_sim.py` | runs the full stack with zero hardware or dependencies — **tested in CI** |
| `sonos.yaml` | topology for a real speaker (set its IP) |

## Run the sim (no speaker, no soco)

```
python demo_sim.py
```

…or see it through the SHAL CLI in one shot — prints live state, no MCP host needed:

```
shal probe examples/demos/sonos/sonos_sim.yaml --drivers examples/demos/sonos/
```

## Run against your speaker

```
pip install soco
```

Edit `sonos.yaml` with your speaker's IP (Sonos app → System → About My System),
then drive it from Python:

```python
import shal, sonos_driver  # noqa: F401  (registers sonos,speaker)

with shal.load("sonos.yaml") as hal:
    spk = hal.get_device("sonos")
    print(spk.get_state(), spk.get_volume(), spk.now_playing())  # reads — free
    spk.pause()                                                  # benign write
```

…or serve it to an AI host as gated tools (this driver is unpackaged, so point
`--drivers` at it so `shal mcp` registers it before loading):

```
shal mcp examples/demos/sonos/sonos.yaml --drivers examples/demos/sonos/
```

## How it maps to SHAL concepts

- **Driver** — `SonosSpeaker` is a **root** driver (`kind = None`): it wraps `soco`
  directly, no SHAL bus. Bound by its `compatible` string `sonos,speaker`.
- **Capability** — `MediaPlayer` Protocol; agent/user code depends on it, not the
  driver class.
- **Sim-first** — `address: sim` selects the in-memory `soco` stand-in in the
  reference's `sim.py`, so
  the whole flow validates without hardware or the `soco` dependency.
- **Side effects** — playback/volume are `side_effect="write"` (benign, reversible,
  instant) so an agent drives them without a per-call approval prompt; reads are
  free. A cautious operator can still install a stricter approver.
- **Errors** — network/`soco` failures map to `HopError(delivered="unknown")`, so
  the agent surface reports a clean, honest failure.
