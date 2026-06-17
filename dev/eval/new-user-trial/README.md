# SHAL new-user trial (device-agnostic)

A lightweight, repeatable way to put SHAL in front of a **brand-new user** — an agent
that installs a packaged build cold and tries to control a **real device** — and capture
an honest **UX/DX report with timing and friction**. Run it before a launch, after an
onboarding change, or any time you want to know "what does a newcomer actually hit?" —
against **any device you own**.

This intentionally replaces the heavier `dev/eval/cold-start` harness: same goal, far less
machinery. It's two fill-in-the-blank templates and a short procedure — not a framework.

## Files
- `TRIAL_BRIEF.template.md` — the agent's protocol/prompt. Fill the `<…>` placeholders
  for your device; the rest is device-agnostic and enforces the honesty rules.
- `REPORT.template.md` — the 6-section report skeleton the agent fills in.
- `reports/` — one saved report per run (`<date>-<device>.md`).

## How to run (per device)
1. **Pack a wheel** so the agent installs the way a real user does:
   `python -m build --wheel`  →  `dist/pyshal-<ver>-py3-none-any.whl`
2. **Turn off per-command approval for the session** — `Shift+Tab` until it shows
   *"bypass permissions"* (or `/permissions`). Without this, background agents can't run
   shell at all and the trial stalls at step one. *(This bit us once; it's the #1 gotcha.)*
3. **Fill `TRIAL_BRIEF.template.md`** — replace the `<…>` placeholders (device name,
   how it's reached, the read-only ops, the wheel path). Anything device-specific the user
   would legitimately know goes here; everything else stays as-is.
4. **Launch a background agent** with the filled brief as its prompt (general-purpose).
   Run one agent per device; they're independent.
5. The agent installs the wheel in a throwaway venv, tries a **read-only** read of the
   **real** device, times each phase, and writes its report from `REPORT.template.md`.
6. **Save the report** to `reports/<date>-<device>.md`. For a leadership summary, distill
   across devices into a short COO-style brief (see the 2026-06-16 example in `reports/`).

## The rules that keep it honest (baked into the brief)
- **Install only from the packed wheel** — not the repo path. Real-install fidelity, and
  it surfaces packaging friction (missing docs, broken links, etc.).
- **Package-only knowledge** — the agent learns from `--help` and shipped docs, **not**
  the repo source tree. (For a *supported* device it may use the bundled driver — that's
  the product. For an *unsupported* one it must build from the device's **public**
  library, never repo-internal device code.)
- **Read-only on the device** — never actuate. Nothing in the home plays or moves.
- **Real device, not the simulator** — a sim read never counts.
- **Stop at the first wall and report it honestly** — a PARTIAL/NO with crisp friction is
  worth far more than a faked success.

## What you get
For each device: did it work, time-to-first-real-read, a UX narrative (newcomer feel), a
DX narrative (install/setup feel), a ranked friction list, and the single biggest
improvement. Across devices, the cross-cutting friction is the launch signal.
