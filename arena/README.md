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

shal-arena check-driver <run-id> psu0 ./driver.py --json
#  -> lights the psu0 tile when your driver passes the ADK-style check
#     (issue #311 Agent path; `check` still works as an alias)

shal-arena measure <run-id> dmm0 ./driver.py --json
#  -> takes YOUR OWN reading through your bound driver: {"reading": 2.9, ...}
#     (issue #312 Agent path — this is what `answer` checks the sim log for)

shal-arena answer <run-id> low_voltage --json
#  -> closes the run; "correct": true/false, plus "disqualified", "score"
#     (the 13-field score file) and "sim_log" (where every `measure` call's
#     SCPI command was recorded)
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
  fault; the probing instrument's reading is bound to a topology generated
  **in memory only** (never written to any file) that wires the realized
  fault into the sim: `low_voltage`/`noise` shift or add ripple to the
  reading, `open` sets `fault: unplugged` (the same sim-only mechanism
  `shal` core ships for issue #304) so the instrument is simply unreachable.
- **`measure` — the player's own reading.** `check-driver` only validates
  your driver (every player runs it, pass or fail, to light the tile); it
  never touches the sim log. `shal-arena measure` is the deliberate act of
  taking a reading through your bound driver, and is the only thing that
  writes to the sim log. Right before the read, it writes one neutral
  `measure` entry (address and time, nothing else — identical whatever
  happens next); a successful read also appends its own `query` entry (same
  format whether it came from the CLI, MCP, or Python — captured at `shal`'s
  own structured bus log). An unreachable instrument (or a driver bug)
  raises `MeasurementFailed` live, to you, after that `measure` entry is
  already written — a file naming *how* a read failed would, in practice,
  name the `open` fault, since nothing else makes a correct driver fail to
  read, so nothing about the failure itself is ever written down.
- **Disqualification.** `shal-arena answer` refuses to credit an answer with
  no `measure` entry at any probe instrument's address: `"disqualified":
  true` means you never called `measure` for one, full stop — not whether
  the read that followed succeeded, so a fault like `open`, unreachable by
  design, can still be answered correctly and counted.
- **Score file.** `shal-arena answer` also writes `<run_id>.score.json`
  (13 fields — `task_id`, `seed`, `fault_type`, `faults_total`,
  `faults_caught`, `false_fails`, `error_fail_correct`, `duration_s`,
  `turns`, `gate_stops`, `schema_version`, `game_version`,
  `record_sha256`), validated against `shal_arena.score.SCORE_SCHEMA`;
  `record_sha256` is the sha256 of `<run_id>.record.json`'s own bytes.

Issue #313 ships the damage model and the generic card simulator
(`card_sim`), and `shal-arena drive` (the Agent path to it).

Issue #314 adds benchmark mode: the same task played with SHAL (today's
`run`/`check-driver`/`measure`/`drive`/`answer`, unmodified) and without it
(raw SCPI access — no driver.py, no gate, no record), on the identical seeded
world, so the two are comparable:

```bash
shal-arena bench src/shal_arena/tasks/rail-3v3.yaml --runs 10 --policy ./policy.py --json
#  -> {"with_shal": {"median_turns": ..., "turns_range": [...], "sim_logs": [...]}, "without_shal": {...}}
```

`--policy` is a Python file defining `play_with_shal(task_path, seed,
state_dir)` and `play_without_shal(...)`, each returning `(run_id, the dict
answer returned)` — your own agent, or a scripted one; `shal-arena` plays no
model of its own (running an actual model across many tasks and publishing
numbers is out of scope for this ticket — see `shal_arena.bench`'s own
docstring). One turn = one call that reaches the sim on either side (`check`,
`measure`, `drive`, or a raw SCPI command, via the new `shal_arena.runner.
raw_scpi`); `answer` is never a turn. `--runs` below 10 is refused, naming the
fix. Results always report both sides — a task where "without SHAL" does
better is never filtered out.

Issue #315 adds the replay/result-card and rack pages, both offline single
HTML files built by `shal_arena.replay`:

```bash
shal-arena answer <run-id> ok --json      # closes the run first
shal-arena replay <run-id> --json         # -> {"card_path": "..."}
#  writes <run-id>.card.html: headline, false-fail count stated openly, the
#  call-by-call replay (a refused `drive` in red), "Copy result" and "Save
#  as image", and the one network reference anywhere on the page
#  (github.com/determlab/shal). Never buildable before `answer` has closed
#  the run — there is no record/score file yet, so it refuses instead.

shal-arena rack --out rack.html           # drag instrument tiles -> setup.yaml
shal-arena setup-yaml scpi-psu dmm --json # the rack's own mechanism, no page
```

The turn count the card shows is `score["turns"]` — the field that already
counts `check`+`measure`+`drive` turns the CTO's ruling on #314 fixed (a
`drive` that refuses or otherwise fails still costs its turn; `answer` never
does).
