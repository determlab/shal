# DoD — pyshal cold-agent release flow (the rubric this harness scores)

## Outcome
The first build a stranger can actually use: a cold AI agent, given only `pyshal` + a
device, completes the whole flow in an **isolated clean env**, for the devices in scope,
with **no major UX pushback**.

## a. Cold-agent flow (per device, end-to-end)
1. **Install** — `pip install pyshal[mcp]` in a fresh venv.
2. **Understand** — from the **in-package guide** only (`shal docs`).
3. **Create the YAML** topology for the device.
4. **Author the driver** — the agent finds the device's docs/library (NOT provided) and
   writes the driver.
5. **Read, ungated** — live data returns freely (empty must raise, never default).
6. **Control, gated** — a physical-actuator write blocks for approval → approve → device
   moves; **deny → device does NOT move**; audit ticket recorded, bound to the id.
   *(Benign-media devices have no actuator: their writes run free — that is correct, and
   the card marks the actuation `benign`; the approve/deny hero is the actuator devices.)*
7. **Control via MCP** — the same control through the `shal mcp` server.

## b. No major UX pushback (binary)
- **No traceback at any step.** Missing topology / missing `[mcp]` extra / read-with-no-data
  → friendly, actionable message.
- The agent needs only the shipped guide + the device's own docs/library — no insider help.

## c. Devices in scope
Listed under `devices/`. Each card states make/model, how it's reached, the Python floor,
and the acceptance (the one actuation + the proving read + whether it's gated).

## 3. Verification
- Run in an **isolated clean env** (fresh venv; container preferred — the agent runs its
  own authored code).
- Evidence = agent transcript + terminal logs + audit ticket.
- `shal.__version__` matches the published tag.
- A green that required weakening/bypassing the gate, self-approving, or moving
  pre-approval counts as **RED**.

## Done
Flow passes end-to-end for every in-scope device, no major UX pushback, evidence attached
→ cut the tag → publish → a clean-venv `pip install pyshal` pulls a **working** build.
