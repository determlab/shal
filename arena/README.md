# shal-arena

SHAL Arena: a public challenge. A test engineer (or their agent) gets a card
description, a question, and a list of simulated instruments; writes a
`driver.py` for each; and answers what's wrong with the card — all through
SHAL. This package is the runner and task format everything else in the
Arena builds on (issue #310).

No account, no hardware: every instrument is a simulator (`shal,sim-scpi`,
already shipped by `pyshal`), and the player's only deliverable per
instrument is `driver.py`.

## Install

```bash
pip install -e ".[dev]"   # from arena/, with pyshal already installed
```

## Play

```bash
shal-arena run src/shal_arena/tasks/rail-3v3.yaml --json
#  -> {"run_id": "...", "task": {...}, "instruments": [...], ...}

shal-arena check <run-id> psu0 ./driver.py --json
#  -> lights the psu0 tile when your driver passes the ADK-style check

shal-arena answer <run-id> low_voltage --json
#  -> closes the run; "correct": true/false
```

Every command is non-interactive and takes `--json`; an error is
`{"ok": false, "error": {"type", "message", "fix"}}` on stdout, `fix` never
empty, matching `shal`'s own `--json` error shape (`AGENTS.md`).

## Task and card format (v1)

Ruled by the CTO on issue #310: two data-only YAML files, no expressions, no
code — a task PR cannot run anything. See
[`src/shal_arena/tasks/rail-3v3.yaml`](src/shal_arena/tasks/rail-3v3.yaml) and
[`src/shal_arena/cards/buck-5v-3v3.yaml`](src/shal_arena/cards/buck-5v-3v3.yaml)
for a worked example, and `src/shal_arena/schema.py` for the validated shape.

## Scope

This ticket ships the runner, the task/card loader, and the CLI — not fault
injection, not a card circuit simulation, and not new instrument simulators
(the two packaged ADK cases, `scpi-psu` and `dmm`, reuse `pyshal`'s existing
`shal,sim-scpi` bus). The fault a run picks is bookkeeping only until a later
arena ticket wires it into the instruments' simulated behaviour.
