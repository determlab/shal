# Cold-start verify — the re-runnable 0.2.x DoD harness

Proves the release promise the way a stranger experiences it: a **cold AI agent**, given
only the published `pyshal` + a device, in an **isolated clean env**, runs the whole flow
— install → understand from the in-package guide → write the YAML → **author a driver
from the device's own docs/library** → read ungated → **drive it gated (approve moves /
deny doesn't)** → same control over MCP. Red/green against [`DoD.md`](DoD.md).

It is **device-agnostic**: the operator/approver prompts name no device. Each device is a
card under [`devices/`](devices/) (make/model/how-it's-reached/acceptance). **A new device
= a new card, zero prompt change** — same principle as the agenticQA manuscripts.

> On-demand **pre-release gate** — LLM in the loop, slow, costs tokens, touches real
> hardware. NOT every-PR CI. The deterministic net for every PR is
> `tests/test_agenticqa_control_loop.py` (#79) + the staged release-acceptance job.

## Run it (same every time)

1. `cp .env.example .env` and fill it (device creds / host). **Never committed** — see
   `.gitignore`.
2. `bash setup.sh` — clones SHAL `main`, builds the candidate tar, makes a **fresh** venv,
   cold-installs `pyshal[mcp]`, prints the version. *(Device libs may need Python 3.11+ —
   see the device card; build the venv on that Python.)*
3. Spawn two **separate** agents (operator must never approve its own write):
   - **operator** — [`operator-prompt.md`](operator-prompt.md) + the device card.
   - **approver** (the human at the gate) — [`approver-prompt.md`](approver-prompt.md).
4. Score against [`DoD.md`](DoD.md). Save the transcript + audit ticket as evidence.

**After any fix:** push to SHAL `main` → re-run from step 2. Same script, same prompts →
same test.

## Files
`setup.sh` · `operator-prompt.md` · `approver-prompt.md` · `DoD.md` ·
`devices/<device>.md` · `.env.example` · `.gitignore`. The operator authors its driver +
topology into the run dir; nothing here ships in the wheel.
