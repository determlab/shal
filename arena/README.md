# shal-arena

SHAL Arena: a public challenge. A test engineer (or their agent) gets a card
description, a question, and a list of simulated instruments; writes a
`driver.py` for each; and answers what's wrong with the card — all through
SHAL. This package is the runner and task format everything else in the
Arena builds on (issue #310).

No account, no hardware: every instrument is a simulator (`shal,sim-scpi`,
already shipped by `pyshal`), and the player's only deliverable per
instrument is `driver.py` — copy
[`examples/reference_driver/driver.py`](examples/reference_driver/driver.py)
as your starting point; it already passes `check-driver` for the `scpi-psu`
case below.

## Install

```bash
pip install -e ".[dev]"   # from arena/, with pyshal already installed
```

## See it run

No clone, no config, no task of your own needed — issue #410 ships the
whole story (a virtual bench pass, a blocked 30 V request, three arena
tasks measured and answered for real, a result card, and the gate-on/off
benchmark) inside the installed package itself:

```bash
shal-arena demo --json
```

prints one JSON document (`{"ok", "record_path", "card_path", "steps", ...}`)
— `record_path` and `card_path` are absolute paths to real files this run
just wrote, ready to open. Drop `--json` to watch it narrate instead
(`--pause 0` skips the pauses between steps either way); see
[`examples/demos/story/README.md`](../examples/demos/story/README.md) for
the full walkthrough and what each step proves.

## Agent path: card, replay, rack and setup.yaml

Every command below is non-interactive, takes `--json`, and is pasted
straight from a real run. `rail-3v3.yaml`'s own `seed:` picks a fixed fault
for anyone who runs the task plain — so this walkthrough passes `--seed 1`
instead, to demonstrate the flow without ever printing (or needing you to
know) which fault the task's own default seed picks. `<run-id>` is the
`run_id` the first command printed — sub it in for every command after. An
error from any command is `{"ok": false, "error": {"type", "message",
"fix"}}` on stdout, `fix` never empty, matching `shal`'s own `--json` error
shape (`AGENTS.md`).

**Tasks** — list the packaged tasks (`easy`, `medium`, `hard`, `rail-3v3`);
each `name` is what `run` takes, with no checkout needed (a path to your own
task.yaml works too). An unknown name errors with the valid names:

```bash
shal-arena tasks --json
```

**Card** — start a run and read its card (the task, the question, and each
instrument's datasheet):

```bash
shal-arena run rail-3v3 --seed 1 --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "run_id": "run-20261005T202840Z-38a82937",
  "task": {
    "id": "rail-3v3",
    "title": "Is the 3V3 rail in spec?",
    "level": "easy",
    "card_description": "Fictional card, no real part. 5 V input, buck to 3.3 V, one test point tp_3v3.",
    "question": "Power the card at 5.0 V. Is the 3V3 rail in spec? Answer ok, or name the fault."
  },
  "instruments": [
    {
      "address": "psu0",
      "case": "scpi-psu",
      "replacement_usd": 1200.0,
      "datasheet": "# Arena Bench PSU-1 ... (full text; see adk/scpi-psu/docs/datasheet.md) ...",
      "drives": "card.vin"
    },
    {
      "address": "dmm0",
      "case": "dmm",
      "replacement_usd": 1200.0,
      "datasheet": "# Arena Bench DMM-1 ... (full text; see adk/dmm/docs/datasheet.md) ...",
      "probe": "card.tp_3v3"
    }
  ],
  "limits": {
    "max_turns": 60,
    "max_minutes": 30
  }
}
```

(`datasheet` is each instrument's full command reference, elided above for
length — it's what you write `driver.py` from; nothing in it or in the
topology behind it is the hidden fault.)

Write (or copy) a `driver.py` per instrument, check it, then take your own
reading through it:

```bash
shal-arena check-driver <run-id> psu0 examples/reference_driver/driver.py --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "run_id": "run-20261005T202840Z-38a82937",
  "address": "psu0",
  "case": "scpi-psu",
  "passed": true,
  "problems": [],
  "warnings": []
}
```

```bash
shal-arena measure <run-id> psu0 examples/reference_driver/driver.py --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "run_id": "run-20261005T202840Z-38a82937",
  "address": "psu0",
  "case": "scpi-psu",
  "op": "measure_voltage",
  "reading": 0.0,
  "card": {
    "state": "ok",
    "applied": {
      "vin": 5.0
    },
    "supply_a": 0.05
  }
}
```

### Write your driver

Every instrument a task names — `scpi-psu` or `dmm` today — needs one small
`driver.py` so SHAL can talk to it, exactly like the reference one above.
This is the whole deliverable per instrument; nothing else in this package
is yours to change.

1. **Start from the matching minimal example.**
   [`examples/minimal_psu_driver.py`](examples/minimal_psu_driver.py) for an
   instrument that `drives` a card input (a setpoint write plus a readback);
   [`examples/minimal_dmm_driver.py`](examples/minimal_dmm_driver.py) for one
   that `probe`s a test point (one read). The `run`'s own JSON above names
   each instrument's `case` and gives you its datasheet — that is the one
   source of truth for the SCPI text, never this package's internals.
2. **Declare it.** A driver class sets `compatible` to the exact string your
   instrument's case uses (`arena,bench-psu1` for `scpi-psu`,
   `arena,bench-dmm1` for `dmm` — both examples already have the right one),
   `kind = MessageTransport`, and `llm_ready = True`. Each op is a plain
   method decorated with `@op("one-line description", unit=..., side_effect=...)`
   — `side_effect="none"` for a read, `"actuator"` for anything that drives
   the instrument now (SHAL gates those; see the main `AGENTS.md`).
   `@idempotent` marks a read, or a write that is safe to resend (an
   absolute setpoint, like `set_voltage` below).
3. **Check one, then measure a different one.** No `override=True` needed
   (arena#394 fixed that for a `bench` run) — but checking and then
   measuring the *same* instrument's driver outside `bench`, in one sitting,
   still re-imports the same file twice and needs it. Sidestep that
   entirely by checking one instrument and measuring another, same as the
   walkthrough above:

   ```bash
   shal-arena check-driver <run-id> psu0 examples/minimal_psu_driver.py --json
   shal-arena measure <run-id> dmm0 examples/minimal_dmm_driver.py --json
   ```

   or run a whole batch with `shal-arena bench` once you have a `--policy`
   file (see "Scope" below, issue #314).

`arena/tests/test_readme_examples.py` runs both minimal examples this same
way, in CI, so copying them keeps working.

**What `open` looks like.** One of the faults a card can hide is `open`: the
instrument simply does not answer — every hop to it raises, the same as a
cut cable. Your driver does not need to detect this itself; just let the
exchange raise, same as `minimal_dmm_driver.py` above already does. `measure`
reports it as a normal failure, never a crash:

```
$ shal-arena measure <run-id> dmm0 examples/minimal_dmm_driver.py --json
shal-arena: measure_voltage raised HopError: no answer from the instrument
at 'dmm0' (hop: sim-scpi, delivered=no)
{
  "ok": false,
  "error": {
    "type": "MeasurementFailed",
    "message": "measure_voltage raised HopError: no answer from the instrument at 'dmm0' (hop: sim-scpi, delivered=no)",
    "fix": "the instrument did not answer this call — if that's unexpected, check your driver.py's handling of the case's SCPI dialect"
  }
}
```

Exit code 1 — the op failed, same family as `shal call`'s own "op failed"
outcome, not a crash in the check machinery. A failure shaped exactly like
this, on an otherwise-correct driver, is itself the signal: answer `open`.

Answer and close the run. `ok` below is a placeholder answer to show the
command's shape — the real method for picking a value is reading the
instruments, not the one this walkthrough happens to pass:

```bash
shal-arena answer <run-id> ok --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "run_id": "run-20261005T202840Z-38a82937",
  "task_path": "/path/to/shal_arena/tasks/rail-3v3.yaml",
  "card_path": "/path/to/shal_arena/cards/buck-5v-3v3.yaml",
  "given": "ok",
  "fault_id": "low_voltage",
  "correct": false,
  "closed_at": "2026-10-05T20:28:47Z",
  "disqualified": true,
  "score": {
    "task_id": "rail-3v3",
    "seed": 1,
    "fault_type": "low_voltage",
    "faults_total": 1,
    "faults_caught": 0,
    "false_fails": 0,
    "error_fail_correct": 0,
    "duration_s": 7.0,
    "turns": 2,
    "gate_stops": 0,
    "schema_version": 1,
    "game_version": "0.4.0",
    "record_sha256": "1a50e0855e36a80e3c4a6cb55cfe7034ef5769a6d58dfef83ff8425ecd39f3ba"
  },
  "sim_log": ".shal-arena/run-20261005T202840Z-38a82937.simlog.jsonl"
}
```

This run is honestly `"disqualified": true`: the reference driver above
only covers `psu0` (which `drives` the card, it doesn't `probe` it), so
`measure` was never called on `dmm0`, the one instrument this task's
question is actually about. Call `measure` on the probe instrument you
intend to answer about to avoid that.

**Replay** — build the offline result card for a closed run:

```bash
shal-arena replay <run-id> --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "run_id": "run-20261005T202840Z-38a82937",
  "card_path": ".shal-arena/run-20261005T202840Z-38a82937.card.html"
}
```

`<run-id>.card.html` is a single offline file: headline, false-fail count
stated openly, the call-by-call replay (a refused `drive` in red), "Copy
result" and "Save as image", and the one network reference anywhere on the
page (github.com/determlab/shal). It refuses instead of writing anything
until `answer` has closed the run — there is no record/score file yet.

**Rack** — drag instrument tiles into a `setup.yaml`, as a page:

```bash
shal-arena rack --out rack.html --json
```

```
{
  "ok": true,
  "side_effect": "write",
  "rack_path": "rack.html"
}
```

**setup.yaml** — the rack page's own mechanism, without the page:

```bash
shal-arena setup-yaml scpi-psu dmm --json
```

```
{
  "ok": true,
  "side_effect": "none",
  "setup_yaml": "shal_version: 1\nroot:\n  bench0:\n    driver: shal,sim-scpi\n    address: sim0\n    children:\n      scpi_psu:\n        driver: arena,bench-psu1\n        address: 1\n  bench1:\n    driver: shal,sim-scpi\n    address: sim1\n    children:\n      dmm:\n        driver: arena,bench-dmm1\n        address: 1\n"
}
```

Pass `--out <path>` to also write that YAML to a file. `setup-yaml` and the
page it backs take any of the packaged case names (`shal-arena setup-yaml
--help`, or see `src/shal_arena/cases.py`'s `CASES`).

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
shal-arena bench --runs 10 --json
```

Issue #397 gives `bench` a built-in default policy, so the command above
works with no file of your own and no task argument — it plays the
built-in sample task (`rail-3v3`) with a policy of its own that
drives the PSU's own max setpoint (30 V) on purpose, takes one reading,
and answers from that reading (not a hardcoded guess). That voltage is
past the card's documented abs max, so this also shows what the two sides
being "comparable" actually buys you: `drive`'s own gate (issue #330)
refuses it, every time, with nothing sent; `raw_scpi` has no gate and
sends it straight through, destroying the card, every time it gets that
far. Not meant to score the *task* well — only to prove the plumbing
works, with a real, non-trivial result.

Pass `--policy` to play your own task, or your own agent, instead:

```
shal-arena bench rail-3v3 --runs 10 --policy ./policy.py --json
#  -> {"with_shal": {"median_turns": ..., "turns_range": [...], "sim_logs": [...]}, "without_shal": {...}}
```

`./policy.py` is one Python file defining `play_with_shal(task_path, seed,
state_dir)` and `play_without_shal(...)`, each returning `(run_id, the dict
answer returned)` — your own agent, or a scripted one; `shal-arena` plays no
model of its own (running an actual model across many tasks and publishing
numbers is out of scope for this ticket — see `shal_arena.bench`'s own
docstring). One turn = one call that reaches the sim on either side
(`check`, `measure`, `drive`, or a raw SCPI command, via
`shal_arena.runner.raw_scpi`); `answer` is never a turn. `--runs` below 10
is refused, naming the fix. Results always report both sides — a task
where "without SHAL" does better is never filtered out.

Issue #315 adds the replay/result-card and rack pages, both offline single
HTML files built by `shal_arena.replay` — see "Agent path" above for a
real, worked example of `replay`, `rack` and `setup-yaml`. The turn count
the card shows is `score["turns"]` — the field that already counts
`check`+`measure`+`drive` turns the CTO's ruling on #314 fixed (a `drive`
that refuses or otherwise fails still costs its turn; `answer` never does).

The `relay-rail` task adds a fourth instrument on a third protocol: `psu0`
and `dmm0` as above, plus `relay0` (a Modbus-framed relay switching the
card's own power, over `shal,sim-msg`, request/reply as plain dicts — no
`pymodbus`, no TCP) and `temp0` (an `sht31`-style temperature sensor on
`shal,sim-i2c`, probing the regulator) — with a fourth fault, `overheat`
(the regulator runs hot while the 3V3 rail still reads nominal, so only an
agent that reads all four instruments gets it right). `relay0`'s coil ops
play through one generic path, `shal-arena call <run-id> <address>
./driver.py <op> [args...] --json`, which runs any op of your own driver
through SHAL's normal gate/limits/approval (AGENTS.md) instead of a
fixed probe/drives pair — e.g. `call <run-id> relay0 ./driver.py set_relay
0 false --json` and `call <run-id> relay0 ./driver.py read_relay 0
--json`; `measure`/`check-driver` are unchanged and still cover the probe
instruments, `dmm0` and `temp0`.
