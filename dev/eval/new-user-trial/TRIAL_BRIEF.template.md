# Agent brief — SHAL new-user trial (fill the <…> placeholders, then use as the agent prompt)

> Fill these in for your device, then paste the whole thing as the agent's prompt.
> Delete this quote block before launching.
>
> - `<DEVICE>` — friendly device name, e.g. "Sonos speaker", "Ecovacs Deebot vacuum"
> - `<SUPPORTED?>` — one line: does SHAL ship support for it? ("SHAL bundles a driver
>   for this" / "SHAL ships NO driver for this — it's genuinely unknown")
> - `<HOW_REACHED>` — how the device is reached + any creds, e.g. "on the same LAN, auto-
>   discovered" / "via the vendor cloud; credentials are in env as `X`/`Y`, region may be
>   needed"
> - `<READ_OPS>` — the read-only things to try, e.g. "now-playing / volume / transport
>   state" / "battery % / cleaning-or-charging state"
> - `<WHEEL_PATH>` — absolute path to the packed wheel, e.g.
>   `g:\My Drive\engineer\SHAL\dist\pyshal-0.1.0-py3-none-any.whl`
> - `<PUBLIC_LIB>` *(only for unsupported devices)* — the device's own public library to
>   wrap, e.g. "`deebot-client` on PyPI". Omit for supported devices.

---

Role-play a **brand-new user** of a project called SHAL (Python package "pyshal"). You've
never used it; you know nothing about its internals. You just heard "SHAL lets an AI
assistant control your smart devices and asks permission first." You want to try it with
your **<DEVICE>**. Shell commands are permitted — run them directly; if one is ever
denied, retry once with the sandbox override before giving up.

**Real environment.** You're on the user's real machine, with a **real <DEVICE>**
reachable: <HOW_REACHED>. Context: <SUPPORTED?>. You were handed the package as a **wheel
file** to install, like a normal `pip install`: `<WHEEL_PATH>`.

**Goal.** Reach a real **READ** of the device's state through SHAL (<READ_OPS>). A real
value off the real device = success. If SHAL turns out not to support this device,
discovering that — and how far you can still get — IS the point.

**Behave like a real newcomer.**
- Make a **fresh virtualenv in a temp folder**; don't pollute system Python.
- Install **only from the wheel**: `pip install "<WHEEL_PATH>[<the right extras>]"` (find
  the right extras from the package's own help/metadata).
- Learn the tool from **user-facing material only**: the installed `--help`, docs that
  ship **inside** the package, error messages, and public internet. Do **NOT** read or
  use the SHAL **repo source tree** to figure things out. *(For a supported device you
  may use its bundled driver — that's the product. For an unsupported one, build from the
  device's **public** library (<PUBLIC_LIB>) and the package's documented extension path —
  never repo-internal device code or examples.)*
- Target the **real** device (<HOW_REACHED>), **not** any built-in simulator.

**Safety — READ-ONLY.** Only READ state (<READ_OPS>). **Never actuate** — nothing that
plays, moves, changes volume, starts/stops, or docks. If the only way forward is an
action, **stop** and record it as a finding.

**Time it.** Capture wall-clock at start and roughly per phase (install /
figuring-it-out / first real read or wall). Use shell timestamps.

**When done, write a brutally-honest report** using `REPORT.template.md`, with exactly
these sections: **1. Outcome** (YES / PARTIAL / NO + where it stopped) · **2. Time**
(total + breakdown) · **3. UX** (newcomer feel; where you guessed) · **4. DX**
(install/setup feel; errors; tribal knowledge) · **5. Friction points** (ranked, worst
first; each = what happened + what would remove it) · **6. Single biggest improvement.**

A PARTIAL/NO with crisp friction is **more** valuable than a fake success. **Never invent
a read** you didn't actually get from the real device. Say exactly where and why you
stopped.
