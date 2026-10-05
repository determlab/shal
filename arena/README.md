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
#  -> closes the run; "correct": true/false, plus "disqualified", "score"
#     (the 13-field score file) and "sim_log" (where every SCPI command this
#     run's `check` calls made was recorded)
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

Issue #310 shipped the runner, the task/card loader, and the CLI — not fault
injection, not a card circuit simulation, and not new instrument simulators
(the two packaged ADK cases, `scpi-psu` and `dmm`, reuse `pyshal`'s existing
`shal,sim-scpi` bus). The fault a run picked was bookkeeping only.

Issue #312 adds the rest of what it takes to actually score a run:

- **Fault injection at run time.** `shal-arena run`'s seed still picks the
  fault; `shal-arena check` now binds the probing instrument to a topology
  generated **in memory only** (never written to any file) that wires the
  realized fault into the sim: `low_voltage`/`noise` shift or add ripple to
  the reading, `open` sets `fault: unplugged` (the same sim-only mechanism
  `shal` core ships for issue #304) so the instrument is simply unreachable.
- **Sim log.** Every SCPI command the sim bus handles during a `check` is
  recorded, with its time, to `<run_id>.simlog.jsonl` next to the run's
  public state — captured at `shal`'s own structured bus log, so the format
  is identical whether the exchange came from the CLI, MCP, or Python.
  `shal-arena answer` refuses to credit a measurement that never happened:
  an answer with nothing logged at a probe instrument's address comes back
  `"disqualified": true`.
- **Score file.** `shal-arena answer` also writes `<run_id>.score.json`
  (13 fields — `task_id`, `seed`, `fault_type`, `faults_total`,
  `faults_caught`, `false_fails`, `error_fail_correct`, `duration_s`,
  `turns`, `gate_stops`, `schema_version`, `game_version`,
  `record_sha256`), validated against `shal_arena.score.SCORE_SCHEMA`;
  `record_sha256` is the sha256 of `<run_id>.record.json`'s own bytes.

Still out of scope: the damage model and the generic card simulator (a later
arena ticket), and the replay page.
