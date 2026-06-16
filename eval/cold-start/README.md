# Cold-start eval — the "unknown hardware" read test

A **reusable, on-demand, read-only end-to-end gate** we run **before every launch**.

It is **not** a pytest and it is **not** wired into CI. CI is hermetic (no real
hardware, no LAN); this harness deliberately touches a real device on the user's
network and is therefore run by a human, by hand, before tagging a release.

## What it measures

A **true cold start**: a brand-new user installs `pyshal` fresh and points it at a
piece of hardware SHAL has *never seen*. An evaluator **agent** then tries to reach a
**read** off that device using only the public package surface, `--help`, the shipped
docs, the documented "author a driver from the device's own docs" path, and the
device's *own* public libraries/docs it can find online.

- **No bundled-driver shortcut, and no simulator.** Sonos ships a driver in the
  package; the agent must treat Sonos as unknown and must **not** import
  `shal.drivers.*`, reference the `sonos,speaker` compatible, install `pyshal[sonos]`,
  or use a built-in `address: sim`. Deebot ships *no* driver at all. This is enforced,
  not just asked: see "Enforcement" below.
- **Read-only.** The only goal is to *read* (Sonos now-playing/volume; Deebot
  battery/clean-state). The agent never actuates, so the approval gate is never
  exercised. This is a "read test".
- **Expected to find a wall.** Supporting arbitrary unknown hardware is SHAL's 1.0
  promise and is **out of scope** for the current launch. The harness's job is to
  measure *how far a cold agent gets and exactly where it stops* — repeatably, over
  time. A FAIL/PARTIAL verdict is a valid, useful result.

**Headline metric:** wall-clock from `pip install` finishing to *a real device
responding to a read* (`install_to_read_ms`), plus the binary `did_it_work`, the
clean-install cost (`install_ms`), and a free-text `wall_reason` (where it stopped).

## Layout

```
eval/cold-start/
  README.md               ← this file
  stage_run.py            ← staging runner (build → venv → guard shim → install → scaffold → print)
  sitecustomize_guard.py  ← copied into the venv as sitecustomize.py; makes the bundled
                            sonos driver + all sims UNRESOLVABLE (enforces the honor rule)
  verify_run.py           ← post-run mechanical verifier (run by the gate operator, AFTER the agent)
  AGENT_BRIEF.md          ← the evaluator agent's protocol (hard rules, checkpoints, integrity)
  templates/
    REPORT.md             ← the Markdown the agent fills in
  metrics.schema.json     ← JSON Schema for the machine-readable metrics
  metrics.sample.json     ← a filled example that validates against the schema
  devices/
    sonos.md              ← per-device card (what the agent is told it has + read ops)
    deebot.md             ← per-device card
  .gitignore              ← ignores runs/ (template + infra tracked, results are not)
  runs/                   ← created on demand; one timestamped dir per run (git-ignored)
```

`stage_run.py` **only** builds, makes a venv, installs, and scaffolds a run dir. It
never runs the evaluation and never touches hardware, so it is safe to invoke on a
machine with no device present (e.g. while developing the harness itself).

## How to run it (on demand, before a launch)

From the repo root, with `python -m build` available (it is a dev dependency; install
it with `pip install build` if missing):

```sh
python eval/cold-start/stage_run.py --device sonos
# or
python eval/cold-start/stage_run.py --device deebot
```

This will:

1. Build the wheel from the current working tree with `python -m build` (so it
   installs **exactly like a real PyPI release**, without publishing). The build is
   made robust against the Google-Drive-on-Windows cleanup race (see below): it clears
   a stale `build/`, builds into a temp dir off the synced drive, and retries once.
2. Create a fresh throwaway virtualenv under the run directory.
3. Drop a guard `sitecustomize.py` into the venv (the bundled-driver/sim wall — see
   "Enforcement").
4. Install **`pyshal[mcp]` only** from the freshly built wheel — **never**
   `pyshal[sonos]` or any device extra (that would be the bundled-driver shortcut the
   cold-start test forbids) — and snapshot `pip freeze` into the run dir.
5. Record the runner-observed install timing (`t_install_start` / `t_install_done` /
   `install_ms`) and build provenance (`env`: pyshal version, wheel, git SHA, platform,
   python version) into a schema-conformant `metrics.json`.
6. Scaffold a timestamped, per-device run directory under `runs/`, seeded from
   `templates/REPORT.md` and that metrics file.
7. Print **exactly what the evaluator agent should do next** (which brief to read,
   which device card, which files to fill in, the venv's `shal-mcp` path, and the
   `verify_run.py` command for the gate operator).

Then hand the printed instructions (and `AGENT_BRIEF.md` + the device card) to the
evaluator agent. The agent fills `report.md` and `metrics.json` in the run dir, stops
at the first wall, and writes an honest verdict. **Afterward, the gate operator runs
the verifier** (see "Enforcement").

### Useful flags

| Flag | Effect |
|------|--------|
| `--device {sonos,deebot}` | which device card / run dir to scaffold (required) |
| `--python <path>` | interpreter to build the venv with (default: the one running the runner) |
| `--keep-existing-dist` | skip the wheel build; reuse the newest wheel already in `dist/` |
| `--runs-dir <path>` | override where run dirs are created (default: `eval/cold-start/runs`) |

### Note for Google-Drive-backed checkouts (this repo's situation)

Building on a Google Drive File Stream working tree can intermittently fail at
setuptools' post-build `rmtree` of `build/bdist.*/wheel` with `WinError 145 (directory
is not empty)`, because the sync daemon briefly holds just-written files open. The
runner mitigates this automatically: it removes a stale `build/` first, builds into a
temp `--outdir` off the drive, and retries the build once (the retry has been reliable
in practice). If a build still fails twice, pause Drive syncing or `rm -rf build/` and
re-run, or pass `--keep-existing-dist` to reuse a wheel already in `dist/`.

## Enforcement (why this is more than an honor system)

The whole point is to measure a *cold* agent, so the bundled-driver and simulator
shortcuts must be real walls, not checkboxes:

1. **Guard shim.** `sitecustomize_guard.py` is copied into the venv as
   `sitecustomize.py`, which Python auto-imports at interpreter startup. It forces
   SHAL's registry to load, then **drops** the bundled `sonos,speaker` compatible and
   every `*,sim*` bus and freezes the registry. After that, any `shal.load` of
   `sonos,speaker` or an `address: sim` device raises the library's own
   `LoadError: no driver installed` — the shortcut is genuinely unreachable inside the
   eval venv.
2. **Real-device evidence in the metrics.** `metrics.schema.json` requires
   `read_attempt.device_address` (a real IP/host, never `sim`/`localhost`) and
   `read_attempt.raw_response` (the verbatim returned value) whenever `did_it_work` is
   true, so a success claim cannot be substantiated without a real read.
3. **Post-run verifier.** After the agent finishes, the gate operator runs:

   ```sh
   <venv python> eval/cold-start/verify_run.py runs/<device>-<timestamp>
   ```

   It (a) validates `metrics.json` against the schema and checks it is `finalized`,
   (b) hard-fails on forbidden tokens or `address: sim` in any AUTHORED machine
   artifact (`*.jsonl`/`*.yaml`/`*.py`) or a leaked `soco` in the recorded `pip
   freeze`, (c) flags ambiguous prose mentions in `report.md` as advisory warnings for
   a human to eyeball, and (d) confirms real-device evidence on a PASS. Non-zero exit
   == the run is rejected as a gate data point. The verdict is therefore not the
   agent's self-report alone.

## Where this sits in the release process

`RELEASING.md` → **Cutting a release** lists this as a **pre-launch e2e gate**: run
`stage_run.py` for each owned device, let the evaluator complete the report, then run
`verify_run.py` **before** bumping the version and tagging. The verdict (and any new
wall) is part of the go/no-go decision. Because the path under test is the out-of-scope
1.0 promise, a PARTIAL/FAIL here does **not** block the launch on its own — it is a
measured, recorded data point that the launch is shipping with that wall known and
documented. (`env.pyshal_version` + `env.git_sha` in each `metrics.json` tie the result
to exactly the build it measured, so results are comparable across launches.)

## Why a top-level `eval/` (not `tests/` or `examples/`)

- `pytest` only collects `testpaths = ["tests"]` (see `pyproject.toml`), so anything
  here is **never imported by pytest** — zero extra config to stay out of collection.
- `tests/e2e/` is the *hermetic* pytest e2e suite (issue #6) and owns the word
  "e2e"; this agent-driven, hardware-touching harness is a different thing — hence
  the name **cold-start**.
- `examples/` are runnable *demos*, not a release gate. A dedicated `eval/` names the
  purpose honestly.

`eval/` is pruned from the sdist (`MANIFEST.in`) and is never packaged into the wheel
(setuptools packages only `src/`), so none of this ships to PyPI.
