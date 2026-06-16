# AGENT BRIEF — cold-start read test (evaluator protocol)

You are the **evaluator agent** for SHAL's cold-start gate. You simulate a
**brand-new user** who just installed `pyshal` and is pointing it at a piece of
hardware SHAL has **never seen**. Your goal: reach a **single read** off that device,
and **measure exactly how far you get and where you stop**.

A staging runner has already built the wheel, made a throwaway venv, installed
`pyshal[mcp]`, recorded the install timing, and scaffolded your run directory. You were
told its path. You will fill in `report.md` and `metrics.json` in that run directory.

This test is **expected to hit a wall.** Supporting arbitrary unknown hardware is
SHAL's 1.0 promise and is out of scope for this launch. A `PARTIAL` or `FAIL` verdict
is a correct, valuable outcome. **Faking success is the only real failure.**

After you finish, a human runs `verify_run.py` against your run directory. It
mechanically rejects the run if you used a forbidden shortcut, used a simulator, or
claimed a read with no real-device evidence. Write as if it will catch you — it will.

---

## Hard rules (do not break these)

1. **No prior knowledge of the codebase.** Approach the device as genuinely unknown.
   Do **not** read SHAL's source tree to learn how a device is wired. Your allowed
   sources are: the installed package's **public surface** (what you can `import` and
   call as a user), `shal-mcp --help` and other CLI `--help`, the **shipped docs**
   (`docs/SDK.md` and the documented driver-authoring path), the `.claude/skills/`
   driver-authoring skills (`shal-generate-driver`, `shal-build-driver`,
   `shal-build-bus`, `shal-build-yaml`), and the **device's own public docs and
   libraries** you find online.

2. **No bundled-driver shortcut, and no simulator.** You must **not**:
   - import or reference `shal.drivers.*` (e.g. `shal.drivers.sonos`);
   - reference the bundled **compatible string** `sonos,speaker` in a topology — the
     core package registers that driver regardless of which extras are installed, so
     loading it via YAML/dict is the *same* shortcut as importing it. Author your **own**
     driver under a **different** compatible (e.g. `community,sonos`) so `shal.load`
     cannot resolve the bundled one;
   - install any device extra (`pyshal[sonos]` and friends are forbidden);
   - use `shal-mcp --device sonos` curated discovery (that *is* the bundled driver);
   - use `address: sim` or any built-in simulator. The read must hit the **real device
     over the network**. A sim returns fabricated, real-looking data — it is never a
     valid cold-start read.

   Sonos *ships* a driver in the package — ignore it completely; treat Sonos as
   unknown. Deebot ships **no** driver — it is genuinely unknown. The only path you
   may use to reach the device is the documented **"author a driver from the device's
   own docs"** path.

   > The scaffolded venv has a guard shim (`sitecustomize.py`) that makes
   > `sonos,speaker` and every `address: sim` device **unresolvable** — `shal.load`
   > of either raises `LoadError: no driver installed`. If you hit that error, you are
   > leaning on the bundled shortcut; that *is* a wall — stop and record it, do not try
   > to defeat the shim.

3. **Read-only on the device.** Attempt only the read ops listed on the device card.
   **Never actuate** (no play/pause/volume-set, no start/stop cleaning, no moving the
   robot). The approval gate is intentionally **not** exercised. If the only way
   forward is a write, that is a wall — stop and record it.

4. **Use only the scaffolded venv.** Run everything through the venv `python` /
   `shal-mcp` the runner printed. Do not fall back to a system install or a different
   environment. (The runner snapshotted `pip freeze` right after install; the verifier
   re-checks that no device extra like `soco` ever entered the venv.)

5. **Stop at the first wall.** When you cannot proceed without breaking a rule, or you
   are genuinely blocked (missing creds, missing bus/transport, undocumented step,
   error with no documented remedy), **stop**. Record the wall honestly. Do not
   invent, guess past, or work around a missing capability to manufacture a read.

---

## Checkpoints (record offset-aware ISO-ms timestamps as you pass each)

Write each timestamp into `metrics.json` under `checkpoints`. Use an **offset-aware**
ISO-8601 millisecond timestamp — `datetime.now().astimezone().isoformat(timespec="milliseconds")`,
e.g. `2026-06-16T14:03:21.250+12:00`. The UTC offset is **required** (the schema rejects
a naive timestamp). The harness deliberately differs from SHAL's `JSONFormatter` here:
the library log emits *naive local* time; the harness records *offset-aware* time so
runs are comparable across machines. Also set `reached_checkpoint` to the **last** named
checkpoint you actually reached.

| Checkpoint key | Owner | Meaning |
|----------------|-------|---------|
| `t_created` | runner | run dir created (already filled) |
| `t_install_start` | **runner** | install began — already filled; **do not edit** |
| `t_install_done` | **runner** | `pip install pyshal[mcp]` finished — already filled; **do not edit** |
| `t_first_read_attempt` | you | you issue the first call that should read the real device |
| `t_first_read_ok` | you | a real device responded to a read (success) |
| `t_wall_hit` | you | you stopped at a wall (set this **instead of** `t_first_read_ok`) |

`t_install_start` / `t_install_done` are **runner-owned and immutable** — the runner
measured them around its own `pip install`, so the headline metric's baseline is real,
not your guess. You only ever fill `t_first_read_attempt` and exactly one of
`t_first_read_ok` / `t_wall_hit`.

The **headline metric** is `install_to_read_ms = t_first_read_ok − t_install_done`.
Compute it only if `t_first_read_ok` is set; otherwise leave it `null` and set
`t_wall_hit` + `wall_reason`. (`install_ms`, the clean-install cost, is already filled
by the runner — leave it.)

`reached_checkpoint` is an enum — use exactly one of:
`installed`, `read_attempted`, `read_ok`, `wall`.

---

## The read attempt — stay on SHAL's audit convention (+ real-device evidence)

When you make the read attempt, record it in `metrics.json` under `read_attempt`
using SHAL's **own audit field names and vocabulary** (so a harness run is comparable
with the library's audit stream), plus two **evidence** fields that prove the read came
off a real device:

- `event`: always `"audit"`.
- `op`: the capability/op name you called (e.g. `now_playing`, `get_volume`, `battery`,
  `clean_state`).
- `outcome`: one of `ok`, `error`, `rejected`, `approved`, `denied`. A successful
  read is `ok`. (Reads are side-effect-free and never gated, so you should only ever
  see `ok` or `error` here.)
- `delivered`: for a read, `"none"` is the expected side effect; on a transport
  failure use the error's `delivered` value (`yes`/`no`/`unknown`) if you have it,
  else `null`.
- `duration_ms`: round-trip of the read, milliseconds (a number).
- `txn`: the 4-char txn id from the library's log if you captured one (see below),
  else `null`.
- `device_address`: the **actual IP/host** you read from. Must be the real device —
  **never** `sim`, `localhost`, `127.0.0.1`, or `::1`. (Required when you claim
  success; the schema and verifier both reject a sim/localhost address on a PASS.)
- `raw_response`: the **verbatim value the device returned** — a track title string, a
  volume integer, a battery percent, a clean-state string. Required when you claim
  success; this is the data that substantiates "a real device responded".

Tip: wrap your read in `shal.logging.capture("debug.jsonl")` to get the library's own
JSON-lines audit record (matching `ts/level/logger/msg + event/op/outcome/...`). Copy
the relevant fields into `read_attempt` so the two streams line up, and leave the
`debug.jsonl` in the run dir as evidence.

---

## What to write in the report

Fill **every** section of `report.md` (the template tells you what each is):

- **Environment** — OS, python version, the wheel version installed, the venv path.
- **Timeline** — the named checkpoints with their timestamps.
- **Per-step log** — each thing you tried, in order, with the exact command/call and
  what happened (verbatim error text where relevant).
- **Metrics** — mirror `metrics.json` as a table.
- **UX narrative** — the *newcomer* feel: was the next step obvious? Where did you
  have to guess? What did `--help` / the README / errors tell you (or fail to)?
- **EX narrative** — the *engineer/driver-author* feel: following the doc→driver path,
  was `docs/SDK.md` + the `shal-generate-driver` skill enough? Where did the
  authoring path break down (missing bus, missing creds, conformance gate,
  registration/install of your local driver)?
- **Blockers** — each wall, what it was, and what would have unblocked it.
- **Verdict** — `PASS` / `PARTIAL` / `FAIL` (see below), one sentence of justification.

### Verdict rubric

- **PASS** — a **real** device (real IP/host, not a sim) responded to a read via the
  doc→driver path, **without** breaking any hard rule, and you captured its returned
  data. (`did_it_work = true`, `verdict = PASS`, `t_first_read_ok` set,
  `read_attempt.device_address` a real host, `read_attempt.raw_response` present.)
- **PARTIAL** — you authored/loaded something and got close (e.g. driver loads,
  topology resolves, transport connects) but no real read landed. (`did_it_work =
  false`, `verdict = PARTIAL`, `reached_checkpoint` ≥ `read_attempted`, `t_wall_hit`
  set.)
- **FAIL** — you could not even get to a read attempt (no documented path, missing
  prerequisite, gave up before authoring), **or** the run is invalid because you broke
  a hard rule. (`did_it_work = false`, `verdict = FAIL`, `reached_checkpoint`
  ∈ {`installed`}, `t_wall_hit` set.)

---

## Integrity self-check (do this before you finalize)

Confirm each, in the report's "Integrity self-check" subsection, with a yes:

1. I did **not** import `shal.drivers.*`, reference the `sonos,speaker` compatible, or
   use `shal-mcp --device`.
2. I did **not** install `pyshal[sonos]` or any device extra — only `pyshal[mcp]`.
3. I did **not** use `address: sim` or any built-in simulator; the read targeted the
   device's **real** network address.
4. I did **not** actuate the device — every op I issued was a read.
5. Every checkpoint timestamp reflects a **real** event, not a backfilled guess (and I
   left the runner-owned `t_install_*` timestamps untouched).
6. If I claim a read succeeded, I can point to the device's **actual returned data**
   (a track title, a volume number, a battery percent) in the per-step log, and
   `read_attempt.device_address` is the real host I read from.
7. If I hit a wall, I recorded it **honestly** and stopped — I did not fabricate a
   read or quietly use the bundled driver / a simulator to "make it pass".

If you cannot truthfully check item 1, 2, 3, 4, or 7, the run is **invalid** — mark the
verdict `FAIL`, explain in `wall_reason`, and do not claim PASS.

---

## When you are done

- `report.md` is fully filled, including the verdict and the integrity self-check.
- In `metrics.json`, set `finalized` to `true` and fill `did_it_work`, `verdict`,
  `reached_checkpoint`, `wall_reason` (or null on PASS), the read/wall checkpoints, and
  `read_attempt` (including `device_address` + `raw_response` on a PASS). Leave the
  runner-owned fields (`env`, `install_ms`, `t_created`, `t_install_*`) as they are.
- `metrics.json` validates against `../../metrics.schema.json` (relative to the run
  dir: `eval/cold-start/metrics.schema.json`). Validate with the venv's installed
  `jsonschema` (a core pyshal dependency):

  ```python
  import json, jsonschema
  schema = json.load(open("eval/cold-start/metrics.schema.json"))
  data = json.load(open("metrics.json"))
  jsonschema.validate(data, schema)   # raises if non-conformant
  ```

- Any `debug.jsonl` flight-recorder file you captured is left in the run dir as
  evidence, alongside the runner's `pip-freeze.install.txt`.
- Tell the gate operator to run the mechanical verifier:

  ```sh
  <venv python> eval/cold-start/verify_run.py <run dir>
  ```
