# agenticQA — two-agent release-acceptance loop (issue #78)

Prove a fresh `pyshal` artifact cold-installs and drives a device **through the
actuation gate** to a **verified** state change — before we publish or demo. This
replaces the manual package checks that cost 4–5 founder loops every release.

It is **device-agnostic**: every run is parameterized by a per-device *manuscript*
(`dev/eval/manuscripts/<device>.yaml`, see [SCHEMA.md](../manuscripts/SCHEMA.md)). A new
device = a new manuscript, zero harness change. deebot is the first worked example;
sonos is the second; Hue / a Pi relay rig come next as just more manuscripts.

## The load-bearing decision: two roles, never one

SHAL's gate raises `approval_required` + an `approval_id` **before any I/O**; a
**separate** `shal_approve` tool executes it ("do not self-approve"). If one agent both
requests *and* approves, the test proves nothing. So the loop has two roles:

- **operator** (`agenticQA`) — drives the write; has **no** approve tool.
- **approver** (`agenticQA-approver`) — the human stand-in; has **no** actuation tool.

Separation is enforced at the **tool level** (each persona's tool allowlist), not by
instruction. The approver is dev/eval only and **never ships in the wheel**.

## Two tiers

| | Tier 1 — CI (every PR) | Tier 2 — pre-launch / demo |
|---|---|---|
| Device | sim (`*_sim.yaml`) | real device |
| Approver | a pluggable `approver_fn` (the `CallableApprover` spirit) | the `agenticQA-approver` agent |
| Cost | seconds, hermetic, deterministic | minutes, real network, real robot |
| Proves | packaging + gate lifecycle + actuation path | "an agent really controls my device" |

**Tier 1 is built and green** — it is the regression net. Tier 2 reuses the exact same
manuscripts and control loop; only the approver swaps from a function to an agent.

### Tier 2 needs **no** new core work

Both Tier-2 agents talk to **one** shared MCP server (e.g. `mcp__deebot__*`, which
exposes both `start_cleaning` and `shal_approve`). One process = one shared pending-ticket
table, so the approver's `shal_approve(id)` finds the operator's ticket. The earlier
worry that this needs persistent tickets (#56) only applies to *two separate* server
processes — the shared-server model sidesteps it.

## Run it

```sh
# Tier 1, in-process (what CI runs):
python dev/eval/agenticqa/run.py --all            # every manuscript, approve + deny
python dev/eval/agenticqa/run.py deebot --deny     # one device, both paths
pytest tests/test_agenticqa_control_loop.py        # the same loop, as the CI gate

# Release-acceptance: drive the loop from a COLD-INSTALLED exact artifact
python dev/eval/agenticqa/run.py --all --from-tarball dist/pyshal-0.2.0.tar.gz
```

Exit code is `0` only if every run passed — usable as a pre-publish gate.

## How it maps to the #78 "Done when"

- **One command, red/green** — `run.py --all` / the pytest.
- **Deny-path asserts the device did NOT move** — `--deny`, verified by read-back.
- **Pass/fail is a deterministic device read-back, not an LLM opinion** — every verdict
  is a `==` on a real read; the approval *decision* may be a function/agent, the
  *verdict* never is.
- **A green that bypassed the gate counts as red** — `expected.gated` is asserted against
  the live tool catalog, and a gated op must (a) return `approval_required`, (b) leave the
  device unmoved pre-approval, (c) show the `requested -> approved|denied` audit trail bound
  to that ticket id. Turn off the gate (`free_writes`) and the run goes red. The staged
  (`--from-tarball`) path also forces `SHAL_APPROVE=gate`, so a leaked env off-switch can't
  silently weaken a release-acceptance run.
- **The test approver never ships in the wheel** — everything here is under `dev/eval/`.

## Files

- `control_loop.py` — the device-agnostic loop (operator + gate + injectable approver_fn
  + manuscript-driven ground-truth verifier). Importable by the pytest and the runner.
- `run.py` — the CLI: Tier-1 in-process, or `--from-tarball` release-acceptance.
- `../manuscripts/<device>.yaml` + `SCHEMA.md` — the per-device specs.
- `tests/test_agenticqa_control_loop.py` — Tier-1 as the CI gate.
- Personas live at user level: `~/.claude/agents/agenticQA.md`, `agenticQA-approver.md`.

> Sibling, not a fork, of the cold-start **read-test** harness (rebased onto main
> separately): that one enforces an "unknown hardware, no sim" honor rule; agenticQA is a
> **control test** that uses the sim deliberately in Tier 1. Same install-staging idea,
> opposite integrity rules — so they are kept separate.
