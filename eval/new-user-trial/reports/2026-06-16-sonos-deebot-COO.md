# SHAL — New-User Field Trial · COO Report
*2026-06-16 · prepared for the COO · read time ~3 min*

## What we did
We put SHAL in front of a **brand-new user — for real.** Two autonomous agents, each
role-playing a cold newcomer with zero prior knowledge, installed SHAL from a packaged
build on a real machine and tried to control two real devices on the home network:

- a **Sonos speaker** — a device SHAL *supports* out of the box, and
- an **Ecovacs Deebot vacuum** — a device SHAL does **not** support.

Everything was **read-only** — nothing in the home was played, moved, or changed. We
measured three things: **did it work, how long did it take, and where was the friction.**

*Integrity-checked:* we verified both agents hit the **real devices** (not the built-in
simulator) and did **not** shortcut through any device code already in the repo. The
Deebot result was built from the device's public library, from scratch.

## Results
| Device | Worked? | Time to a real reading | The catch |
|---|---|---|---|
| **Sonos** (supported) | ✅ Yes | ~4.5 min | Only reachable by someone who can hand-wire an MCP client |
| **Deebot** (unsupported) | ✅ Yes\* | ~9 min | The user had to **write a device driver themselves** |

Both reached a genuine reading off real hardware (Sonos: now-playing + volume on two
speakers; Deebot: battery 100%). **But neither could do it the way a normal,
non-engineer user would.**

## The headline
**The engine works. The front door doesn't.**

SHAL's core performed well on real hardware — it auto-discovered both speakers in **1
second**, the safety gate behaved correctly, and a cold agent built working vacuum
support in **9 minutes**. The problem is the on-ramp: a genuine new user gets stuck at
the very first step — *"how do I get a reading?"* — because:

1. **No human-runnable command.** The only entry point is a background "MCP server."
   Run it directly (the obvious move) and you get a cryptic error with no next step. You
   only get a reading if you've already wired SHAL into an AI assistant like Claude.
2. **No "add your device" path.** For an unsupported device, nothing tells the user how
   to add one — they're left at a dead end unless they can write code.
3. **The how-to docs don't ship with the product**, and the documentation links are
   **broken** for anyone who installed normally.

## Risk to the launch
The "wow" demo is real — but today it only lands for users who are **already fluent in
MCP and have an AI host configured.** A normal new user, even a technical one, hits a
wall *before* the wow. That directly undercuts the **"install → it works in 2 minutes"**
promise the launch is built on.

## Recommended fixes (small, high-leverage — none are engine work)
1. **A one-shot, human-runnable read** — e.g. `shal-mcp --device sonos --probe` that
   discovers the device and prints the reading. Turns *"stuck at an error"* into *"saw my
   speaker in 60 seconds."* **(Biggest single win.)**
2. **Ship the docs inside the package** and fix the broken links.
3. **An "unknown device → here's the 30-line path to add it" signpost** at the wall.
4. *(Lower)* document the tool-naming quirk and an async/cloud-device pattern.

## Bottom line
**Encouraging:** the hard part — discovery, the safety gate, the device model — already
works on real hardware. The gap is the **on-ramp and packaging**, which is cheap to fix
and high-impact. We recommend closing fixes **1–3 before a public launch**; they convert
a demo that only engineers can reproduce into the 2-minute experience the product
promises.

---
*Method note: autonomous agents on a real Windows PC + real LAN/cloud devices, read-only,
honest-reporting (a partial/failed result with clear friction was preferred over a faked
success). Full per-device reports available on request.*
