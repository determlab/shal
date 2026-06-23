# Live MCP loop — reproducible Tier-2 gate over the shipped `shal mcp` adapter

`live_mcp_loop.py` drives a **real device through the actuation gate over the MCP wire**
(DoD step 7), using the cold-installed artifact's `shal mcp` server. It needs **no host
MCP mount and no Claude restart** — it spawns the server as a subprocess and speaks the
MCP protocol to it as a client. Verdict is a deterministic device read-back.

## Why this exists
A Claude/host MCP client can only reach a device once the host mounts the server and the
session reloads. That's a manual step we can't take mid-turn. This script proves the same
shipped bridge deterministically, from one command, so a release can be gated in CI-like
fashion. (The *two-live-agents* variant — `agenticQA` operator + `agenticQA-approver`
approver — is strictly more faithful but DOES need the host mount + reconnect; see below.)

## Prerequisites (the "rig")
1. A **fresh venv** with the candidate cold-installed: `pip install <pyshal-X.Y.Z.tar.gz>[mcp]`
   plus the device's own library (e.g. `pip install --only-binary=:all: deebot-client`).
   Must be Python ≥ the device library's floor (deebot-client → 3.11+).
2. An **authored driver + topology** for the device, and a gitignored **`.env` beside the
   topology** holding creds (`${VAR}` resolved at load, #73). Never commit `.env`.
3. A **manuscript** (same schema as `../manuscripts/SCHEMA.md`) whose tool names are the
   runtime `<node>__<op>` handles. Example: the cold-authored
   `deebot_n20.manuscript.yaml` (topology + driver are relative to the manuscript).

## Run it (one command — reproducible)
Run with the **cold-installed venv python** (it provides `mcp` + `yaml`):

```sh
RIG=C:/Users/<you>/shal-cold-run
"$RIG/.venv/Scripts/python.exe" dev/eval/agenticqa/live_mcp_loop.py \
    --manuscript "$RIG/deebot_n20.manuscript.yaml" \
    --deny \
    --out "$RIG/evidence"
```

- It runs the **approve** path (robot must move), tears down to safe (stop + dock), then
  the **deny** path (robot must stay docked).
- Evidence is written to `--out/evidence.json` + `evidence.md` (states before/after,
  approval_ids, approver results, per-run reason). Exit code `0` only if every run passed.

## Integrity (a green that breaks any of these is RED)
- **Role separation is enforced in code.** The OPERATOR allowlist = reads + actuators; the
  APPROVER allowlist = `shal_approve`/`shal_deny` only. Either crossing its allowlist aborts
  the run. One process = one shared ticket table, so `shal_approve(id)` finds the operator's
  ticket (no #56 needed) — but it is one process, *not* two separate live agents.
- **No move pre-approval**: the script asserts the gate returned an `approval_id` and that
  the read-back had NOT changed before approval.
- **Deterministic verdict**: `expected.becomes` after approve, `deny_path.stays` after deny —
  device read-back, never an LLM opinion.
- **Never weaken the gate to pass.** No `SHAL_APPROVE=auto`, no self-approve.

## The two-live-agents variant (highest fidelity, manual setup)
To prove "a separate live *agent* controls my device", point a host `deebot` MCP server at
the same topology + driver, then in the host: save config → `/mcp` → reconnect `deebot`
(or restart). With the tools mounted, spawn `agenticQA` (operator) + `agenticQA-approver`
(approver) — their allowlists must match the served `<node>__*` handles. Same manuscript,
same read-back verdict; the only delta is the approver is a live agent instead of a code role.
