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
pip install pyshal
```

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
- `shal routes sim.yaml ambient_temp --json` — the routes a node declares, in order
  (a node without `routes:` shows its one main route). On a node with routes, each
  tool in `shal tools --json` has `routes`, and `shal call ... --via <name>` pins one
  route for the call; over MCP the tool takes an optional `via`. An unknown name is
  an error that lists the valid ones.
- `shal check shal,sim-sensor --json` — check a driver against the authoring
  contract. Exit 0 no problems, 1 problems, 2 the check could not run.
- `shal records [DIR] --verdict fail --json` — read the record store under
  `DIR` (default `.`; `DIR/records.db`), filtered, newest first. Read-only. A
  missing store is exit 1 with a `fix`; a newer `record_version` refuses the
  read unless `--skip-newer`.
- `shal docs` — the guide to add a device. `shal docs --list` lists the reference
  drivers; `shal docs --example tmp102` prints one (driver, sim twin, test, topology).
  A new driver starts from one of these. [docs/CATALOG.md](docs/CATALOG.md) is where
  a new driver goes and what to name it (its `vendor,part` compatible).
- `shal docs --samples` — the samples: small programs a person runs to see what SHAL
  does. `shal docs --sample <name> --to DIR` writes one into a new or empty folder and
  prints the one command that runs it. Samples are not references, and not in `--list`.
- `shal mcp sim.yaml` — serve the same tools to an MCP host over stdio. Needs the
  extra: `pip install "pyshal[mcp]"`.
- Python: `hal = shal.load("sim.yaml")`, then `hal.tool_schemas()` and
  `hal.call_tool("ambient_temp__read_celsius", {})` → `{'ok': True, 'result': 26.33}`.

`--json`: `shal probe`, `shal tools`, `shal docs --list`, `shal docs --samples`,
`shal call`, `shal routes`, `shal check` and `shal records` take it. Each prints one JSON document on stdout; `--help` shows its shape. On an
error the exit code is the same, the message is on stderr, and stdout holds
`{"ok": false, "error": ...}`. `shal probe --json` lists the writes it did not run,
each with the `shal call` line that runs it.

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

`shal call` has no `--approve` flag: the agent that runs a command cannot approve its own
call. A person approves through an MCP host (`shal mcp`), or code runs it inside
`with shal.approver(<your Approver>):` in Python.

Before any gated op in Python, set an approver; with none, a headless run is denied
and a terminal run asks a person.

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
- [RELEASING.md](RELEASING.md) — the two release paths: type A (a version-bump +
  changelog PR, anyone can open) and type B (tag + `gh release create`, founder
  only — the one step that publishes to PyPI).
