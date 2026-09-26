---
type: agent-guide
owner: repo-agent
scope: repo/shal
reviewed: 2026-09-27
---

# AGENTS.md — using SHAL

## What shal does

SHAL reads one YAML file that describes devices and services, and turns every
operation on them into a typed tool you can call from the `shal` CLI, MCP or Python.
Each tool carries its side effect: reads run at once; ops that reconfigure or act
stop for a person.

## Install

```bash
pip install git+https://github.com/determlab/shal
```

**Until the next release, install from the repo.** `pip install pyshal` gives
0.2.2 from PyPI, which has no `shal call`, no `shal check`, no `shal docs --list` /
`--example`, and no `shal,sim-sensor` driver, so the first-success step below fails
on it. When 0.3.0 ships, this line becomes `pip install pyshal`.

## First success

No account, no API key, no hardware. The command needs one file: a topology you
write yourself (a simulated bus with a simulated sensor). Save it as `sim.yaml`:

```yaml
shal_version: 1
root:
  bus:
    driver: shal,sim-i2c
    address: sim0
    children:
      temp0:
        id: ambient_temp
        driver: shal,sim-sensor
        address: 0x48
```

```bash
shal probe sim.yaml
```

```
# 1 read(s), 1 write(s) on this topology
ambient_temp__read_celsius: 25.57
# writes — not run by `shal probe`; use `shal call` (gated ops are refused until approved): ambient_temp__set_target
```

Exit 0. The number drifts on each run (a simulated room). The read ran; the
write did not.

## How an agent calls it

- `shal tools sim.yaml` — list every tool with its kind: `read`, `write` or `gated`.
- `shal probe sim.yaml [tool]` — run all reads, or one.
- `shal call sim.yaml ambient_temp read_celsius --json` — run one op. Reads and
  `write` ops run. Exit codes: 0 ran, 1 the op failed, 2 refused by the gate
  (nothing sent), 3 could not run.
- `shal check shal,sim-sensor --json` — check a driver against the authoring
  contract. Exit 0 no problems, 1 problems, 2 the check could not run.
- `shal docs` — the guide to add a device. `shal docs --list` lists the reference
  drivers; `shal docs --example tmp102` prints one (driver, sim twin, test, topology).
  A new driver starts from one of these. [docs/CATALOG.md](docs/CATALOG.md) is where
  a new driver goes and what to name it (its `vendor,part` compatible).
- `shal mcp sim.yaml` — serve the same tools to an MCP host over stdio. Needs the
  extra: `pip install "pyshal[mcp] @ git+https://github.com/determlab/shal"`.
- Python: `hal = shal.load("sim.yaml")`, then `hal.tool_schemas()` and
  `hal.call_tool("ambient_temp__read_celsius", {})` → `{'ok': True, 'result': 26.33}`.

`--json`: `shal call` and `shal check` take it. `shal probe` and `shal tools` print
text only.

## Side effects

Every op declares `side_effect`, one of four labels. `shal call --json` returns it;
`shal tools` shows it as `read`, `write` or `gated`.

- `none` — a read. It returns a live value or raises; it never returns a stale default.
- `write` — changes only this node's own data, and the driver can undo it. Runs.
- `config` — changes what the system does next (a setting, a schema, a schedule). Gated.
- `actuator` — acts now, outside the node, or cannot be undone. Gated.

There is one gate for both gated labels. `shal call` refuses a gated op with exit 2
and JSON `"rejected": "approval", "sent": false`:

```bash
shal call sim.yaml ambient_temp set_target 30 --json
```

There is no `--approve` flag: the agent that runs a command cannot approve its own
call. A person approves through an MCP host (`shal mcp`), or code runs it inside
`with shal.approver(<your Approver>):` in Python.

## Where the contract lives

- [docs/ARCHITECTURE.md §5](docs/ARCHITECTURE.md) — the Decision Ledger of record.
  It is in this file, not in `docs/DECISIONS.md`. D4 (one gate), D12 (read freshness)
  and D24 (the four labels) bind every call.
- [src/shal/AGENT_GUIDE.md](src/shal/AGENT_GUIDE.md) — how to add a device; it
  ships in the package as `shal docs`.
- [src/shal/SDK.md](src/shal/SDK.md) — the full driver and bus contract
  (`shal docs --sdk`).
- [src/shal/schema/shal-v1.schema.json](src/shal/schema/shal-v1.schema.json) — the
  topology file's schema.
- Contributing to this repo, not using it: [docs/agents/context.md](docs/agents/context.md).
