"""The D2 clean-machine end-to-end story (shal#340).

A buyer's machine has nothing of ours on it. By the time this runs, a shell
step has already assembled a directory of release-candidate wheels (pyshal,
shal-arena, pytest-shal, bricks-engine — one per repo's own D1 `rc-wheels.yml`)
and installed them into a fresh venv with ``pip install --no-index
--find-links <dir> ...``: that call can only ever satisfy itself from what is
already in ``<dir>``, so there is no way for it to silently reach an index
instead (the "no index during the story" rule holds by construction, not by
watching a log).

This script then plays the same two demos a buyer would — the virtual bench
(the ``virtual-bench`` sample packaged inside pyshal, written out with ``shal
docs --sample virtual-bench --to <dir>``, #384) and SHAL Arena (``arena/``) —
entirely through that venv's own ``python`` (and its own ``shal``), and writes
one ``evidence.json``: which OS, which Python, which commit of each repo, and
pass/fail for every check.

Two kinds of function live here:

- The ``check_*`` functions are pure: given the raw JSON (and exit code)
  a step already produced, they decide pass/fail. ``tests/test_e2e_story.py``
  calls these directly against a real, committed ``evidence.json`` (Done-when:
  "no hand-written fixture") — no venv, no subprocess, no network.
- Everything else (``run_virtual_bench_pass``, ``run_arena_task_score_file``,
  ``run_arena_bench_destroyed``, ``main``) talks to the real world: a venv's
  python, the demo files on disk. These only run for real inside the CI job
  (or by hand on a real machine) — never under pytest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
ARENA_TASKS_DIR = REPO_ROOT / "arena" / "src" / "shal_arena" / "tasks"
ARENA_TASK_LEVELS = ["easy", "medium", "hard"]

# shal#300's Unreachable exit code; the virtual-bench sample's run_bench.py's
# own vocabulary.
EXIT_PASS = 0
EXIT_UNREACHABLE = 4

def _reference_dmm_driver_path():
    """The same packaged reference driver `shal_arena.bench`'s own default
    policy uses (`arena/src/shal_arena/_default_dmm_driver.py`) -- resolved
    through the venv that is actually running this call, not a literal copy
    kept in sync by hand (CTO review on #403 round 2). Imported lazily,
    inside this function only: this module is also loaded directly by
    `tests/test_e2e_story.py` under the dev venv at plain import time, which
    may not have shal_arena installed at all, so the top level never imports
    it."""
    import importlib.resources as importlib_resources

    ref = importlib_resources.files("shal_arena") / "_default_dmm_driver.py"
    return importlib_resources.as_file(ref)


#: issue #403 CTO review round 2: a single reading cannot tell `noise` from
#: `ok` -- the sim gives a DIFFERENT reading each call (#433), so one read
#: can easily land inside the tolerance band by chance (CTO: 17 of 17 noise
#: seeds across easy/medium/hard failed with a single read). Several reads,
#: and the SPREAD across them, is what noise actually looks like.
#: CTO review round 3: 5 reads passed 90/90 seeds but with a thin margin on
#: `hard` (band 0.066 V, smallest observed noise spread 0.0748 V) -- 8 widens
#: that margin without exposing the fault's own ripple_vpp (which the run
#: JSON must never leak).
_N_READS = 8

#: issue #477: a rail reading below this fraction of its nominal is `open`
#: (same threshold as `shal_arena.demo.OPEN_FRACTION`).
OPEN_FRACTION = 0.05


def _measure_failure_cause(measure_doc: dict[str, Any]) -> str:
    """``transport`` when a failed `measure` raised shal's own "the hop never
    completed" (`HopError`/`HopTimeout`, named in its error message, the same
    split `runner.take_measurement` logs); ``driver`` for anything else, such
    as a bug in the driver itself (CTO review on #480)."""
    message = ((measure_doc or {}).get("error") or {}).get("message", "")
    return "transport" if ("HopError" in message or "HopTimeout" in message) else "driver"


#: issue #495: a card drawing at least this much supply current works (same
#: threshold as `shal_arena.demo.PROBE_MIN_CURRENT_A`); an `open` one draws ~0 A.
PROBE_MIN_CURRENT_A = 0.01


def _diagnose(nominal_v: float, tol_pct: float, readings: list[float],
             allowed: set[str], current_a: float | None = None) -> str:
    """A guess from the measurements alone (same logic as `shal_arena.demo`'s
    own `_diagnose`, parametrized on the rail's numbers instead of a `Rail`
    object this script has no import for, and on several readings instead of
    one): the rail's own documented nominal voltage and tolerance, never the
    hidden fault. `readings` empty means a measure call failed: a broken
    link to the bench, `error` -- never `open`, never a card fault (issue
    #477). A last reading near 0 V is `open` (an open circuit on the card
    still answers), never `low_voltage`. A spread across readings wider
    than the tolerance band is `noise`, checked before the band itself -- a
    single one of those readings can still land inside the band by chance.
    Otherwise, the last reading outside the band but not clearly low or high
    falls back to `noise` when the task even offers it."""
    if not readings:
        return "error"
    if (abs(readings[-1]) < OPEN_FRACTION * nominal_v and "probe" in allowed
            and current_a is not None and abs(current_a) >= PROBE_MIN_CURRENT_A):
        return "probe"      # issue #495: ~0 V but the card draws its normal current
    if abs(readings[-1]) < OPEN_FRACTION * nominal_v and "open" in allowed:
        return "open"
    band = nominal_v * tol_pct / 100
    if max(readings) - min(readings) > band and "noise" in allowed:
        return "noise"
    delta = readings[-1] - nominal_v
    if abs(delta) <= band:
        return "ok"
    if delta < 0 and "low_voltage" in allowed:
        return "low_voltage"
    if delta > 0 and "high_voltage" in allowed:
        return "high_voltage"
    if "noise" in allowed:
        return "noise"
    return "ok"

_BENCH_POLICY = '''\
"""Scripted #340 story policy: both sides attempt the same 30 V setpoint on
psu0 (the card's 'vin'). SHAL's own gate (arena#338) refuses it; the raw
side has no gate and destroys the card -- proving the gate matters, not
just that it exists."""
from shal_arena.runner import answer, drive_input, pick_fault, raw_scpi, start_run
from shal_arena.loader import load_task


def _fault_answer(run_id, task_path, seed, state_dir):
    card = load_task(task_path).card
    return answer(run_id, pick_fault(card, seed), state_dir=state_dir)


def play_with_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 30.0, state_dir=state_dir)
    return run_id, _fault_answer(run_id, task_path, seed, state_dir)


def play_without_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=state_dir)
    return run_id, _fault_answer(run_id, task_path, seed, state_dir)
'''


def _result(check_id: str, ok: bool, log: str, rerun: str) -> dict[str, Any]:
    # CTO review on #340: a failed check's log must START with the exact
    # command to rerun it (the D2 spec's own Agent path) -- an agent reading
    # evidence.json acts on this without having to reconstruct it.
    if not ok:
        log = f"rerun: {rerun}\n{log}"
    return {"id": check_id, "result": "pass" if ok else "fail", "log": log}


# -- pure checks (Done-when: "every check reads JSON output, never text") --


def check_virtual_bench_pass(doc: dict[str, Any], exit_code: int, rerun: str) -> dict[str, Any]:
    ok = doc.get("verdict") == "pass" and exit_code == EXIT_PASS
    return _result("virtual_bench_pass", ok, json.dumps(doc), rerun)


def check_virtual_bench_unplug_dmm(doc: dict[str, Any], exit_code: int,
                                   rerun: str) -> dict[str, Any]:
    ok = (doc.get("verdict") == "error" and doc.get("cause") == "transport"
         and exit_code != EXIT_PASS and exit_code == EXIT_UNREACHABLE)
    return _result("virtual_bench_unplug_dmm", ok, json.dumps(doc), rerun)


def check_arena_score_file(level: str, score_exists: bool, answer_doc: dict[str, Any],
                           rerun: str) -> dict[str, Any]:
    """issue #403: a score file existing proves nothing on its own -- the old
    check passed on `turns: 0, faults_caught: 0` cells because `answer`
    writes a score file even for a run nothing ever measured. The run must
    actually have been played: `correct` and not `disqualified` (`answer`'s
    own verdict), at least one turn logged, and -- only on a task whose
    realized fault was not `ok` (`score["fault_type"]`, same test
    `build_score`'s own `caught` uses) -- at least one fault caught."""
    score = answer_doc.get("score") or {}
    # `broken_probe` is a good card (issue #478): no card fault to catch
    has_fault = score.get("fault_type") not in (None, "ok", "broken_probe")
    ok = (score_exists and bool(score)
         and answer_doc.get("correct") is True
         and answer_doc.get("disqualified") is False
         and score.get("turns", 0) > 0
         and (not has_fault or score.get("faults_caught", 0) > 0))
    return _result(f"arena_{level}_score_file", ok, json.dumps(
        {"score_file_exists": score_exists, "score": score,
         "correct": answer_doc.get("correct"), "disqualified": answer_doc.get("disqualified")}),
        rerun)


def check_arena_bench_destroyed(doc: dict[str, Any], rerun: str) -> dict[str, Any]:
    with_destroyed = doc.get("with_shal", {}).get("destroyed")
    without_destroyed = doc.get("without_shal", {}).get("destroyed")
    ok = with_destroyed == 0 and isinstance(without_destroyed, int) and without_destroyed > 0
    return _result("arena_bench_destroyed", ok, json.dumps(
        {"with_shal_destroyed": with_destroyed, "without_shal_destroyed": without_destroyed}),
        rerun)


def expected_realized_fault(card_faults: list[dict[str, Any]], seed: int) -> dict[str, Any]:
    """The fault ``seed`` realizes from ``card_faults`` (a card's own
    ``faults:`` list, as YAML gives it), worked out here with the standard
    library alone -- the same draw ``shal_arena.fault.realized_fault`` makes
    for a 0.4.2 run: one ``random.Random(seed).choice`` over the card's
    faults in order, then the ``noise`` ripple rescale. Pure: no shal_arena
    import, so ``tests/test_e2e_story.py`` can call it without the package."""
    import random

    rng = random.Random(seed)
    fault = rng.choice(card_faults)
    noise_scale = rng.uniform(0.7, 1.3)
    extra = {k: v for k, v in fault.items() if k != "id"}
    if fault["id"] == "noise" and "ripple_vpp" in extra:
        extra["ripple_vpp"] = extra["ripple_vpp"] * noise_scale
    return {"fault_id": fault["id"], "extra": extra}


def check_arena_fault_determinism(doc: dict[str, Any], card_faults: list[dict[str, Any]],
                                  seed: int, rerun: str) -> dict[str, Any]:
    """arena#390: ``shal-arena verify``'s replay trusts `fault.realized_fault`
    (a pure ``random.Random(seed)`` computation) to give the SAME fault on
    Windows, Linux and macOS for the SAME seed -- this is what "proves
    identical replay numbers for the same seed on the 3-OS CI matrix" means
    here (arena#390's own Done-when), reusing this already-3-OS harness
    rather than a new workflow. issue #499: the expected fault follows the
    card's own ``faults:`` list (`expected_realized_fault`), never a
    hard-coded id, so a card or challenge change cannot leave it stale."""
    want = expected_realized_fault(card_faults, seed)
    ok = doc.get("fault_id") == want["fault_id"] and doc.get("extra") == want["extra"]
    return _result("arena_fault_determinism", ok,
                   json.dumps({"got": doc, "want": want, "seed": seed}), rerun)


def check_breaking_path(path_id: str, doc: dict[str, Any], exit_code: int, stderr: str,
                        rerun: str) -> dict[str, Any]:
    """issue #341: a path broken on purpose must fail LOUDLY and usefully --
    a non-zero exit, a machine-readable ``error`` ({type, message}) and a
    non-empty ``fix`` naming the command or change, and never a traceback
    (on stdout or stderr). Reads JSON only."""
    error = doc.get("error")
    ok = (exit_code != EXIT_PASS and doc.get("ok") is False and isinstance(error, dict)
         and bool(error.get("type")) and bool(error.get("message"))
         and isinstance(error.get("fix"), str) and bool(error["fix"].strip())
         and "Traceback" not in stderr and "Traceback" not in json.dumps(doc))
    return _result(f"breaking_{path_id}", ok, json.dumps(
        {"exit_code": exit_code, "error": error}), rerun)


def check_repeat_identical(docs: list[dict[str, Any]], exit_codes: list[int], runs: int,
                           rerun: str) -> dict[str, Any]:
    """issue #341: ``runs`` repeats of the virtual-bench story give the SAME
    ``verdict`` and ``cause`` (and exit code) every time -- and that result is
    the passing one; one differing run in ``runs`` fails the check."""
    seen = sorted({(d.get("verdict"), d.get("cause"), c)
                   for d, c in zip(docs, exit_codes, strict=False)}, key=repr)
    ok = (len(docs) == runs and len(exit_codes) == runs and len(seen) == 1
         and seen[0] == ("pass", None, EXIT_PASS))
    return _result("virtual_bench_repeat", ok, json.dumps(
        {"runs": len(docs), "distinct_verdict_cause_exit": [list(s) for s in seen]}), rerun)


def seed_summary(doc: dict[str, Any]) -> str:
    """The part of a ``shal-arena bench --json`` document that must be the
    same on every OS for the same seed: the counts and turn statistics of both
    sides, serialized canonically. The ``task`` and ``sim_logs`` paths are
    machine-specific, so they are left out."""
    def side(s: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in s.items() if k != "sim_logs"}
    return json.dumps({"ok": doc.get("ok"), "runs": doc.get("runs"),
                       "with_shal": side(doc.get("with_shal", {})),
                       "without_shal": side(doc.get("without_shal", {}))},
                      sort_keys=True, separators=(",", ":"))


def check_seed_summary(doc: dict[str, Any], exit_code: int, seed: int,
                       rerun: str) -> dict[str, Any]:
    summary = seed_summary(doc)
    ok = exit_code == EXIT_PASS and doc.get("ok") is True
    return _result(f"arena_bench_seed{seed}", ok, json.dumps(
        {"seed": seed, "summary": summary,
         "sha256": hashlib.sha256(summary.encode("utf-8")).hexdigest()}), rerun)


def compare_seed_evidence(evidences: list[dict[str, Any]], seed: int = 7,
                          expected: int = 6) -> tuple[bool, str]:
    """issue #341, the final job: every evidence.json (one per OS x Python
    cell) holds a passing seed-summary check, and its summary is
    byte-identical to all the others'."""
    check_id = f"arena_bench_seed{seed}"
    summaries: dict[str, list[str]] = {}
    problems: list[str] = []
    for ev in evidences:
        label = f"{ev.get('os')}/{ev.get('python')}"
        found = next((c for c in ev.get("checks", []) if c.get("id") == check_id), None)
        if found is None or found.get("result") != "pass":
            problems.append(f"{label}: no passing {check_id} check")
            continue
        summaries.setdefault(json.loads(found["log"])["summary"], []).append(label)
    if len(evidences) != expected:
        problems.append(f"expected {expected} evidence.json files, got {len(evidences)}")
    if len(summaries) > 1:
        problems.append("summaries differ: " + json.dumps(summaries, sort_keys=True))
    return (not problems), ("; ".join(problems) or
                            f"all {len(evidences)} cells: byte-identical seed {seed} summary")


def check_wheel_installed(package: str, version: str | None, ok: bool, output: str,
                          rerun: str) -> dict[str, Any]:
    return _result(f"wheel_installed_{package}", ok, f"version={version} output={output!r}",
                   rerun)


def check_arena_demo(doc: dict[str, Any], exit_code: int, record_exists: bool,
                     card_exists: bool, rerun: str) -> dict[str, Any]:
    # issue #410 Done-when: "the same test runs in the clean-machine story" --
    # `shal-arena demo` works with no clone and no config, and its JSON
    # names real files, from this machine's own (freshly installed, no
    # checkout) wheels, not just from a pytest venv.
    ok = (exit_code == EXIT_PASS and bool(doc.get("ok")) and record_exists and card_exists)
    return _result("arena_demo", ok, json.dumps(
        {"ok": doc.get("ok"), "record_path": doc.get("record_path"),
         "record_exists": record_exists, "card_path": doc.get("card_path"),
         "card_exists": card_exists}), rerun)


# -- the real world: runs through the clean venv's own python -------------- #


def _run_json(venv_python: str, argv: list[str], cwd: Path | None = None) -> tuple[dict, int]:
    proc = subprocess.run([venv_python, *argv], cwd=cwd, capture_output=True, text=True,
                          timeout=120)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    return doc, proc.returncode


def _argv_str(argv: list[str]) -> str:
    return " ".join(argv)


def _venv_shal(venv_python: str) -> str:
    """The `shal` console script next to `venv_python` — the clean venv's own
    install, never one found elsewhere on PATH."""
    bindir = Path(venv_python).resolve().parent
    shal = shutil.which("shal", path=str(bindir))
    if shal is None:
        raise RuntimeError(f"no `shal` next to {venv_python} in {bindir}")
    return shal


def run_virtual_bench_checks(venv_python: str, bench_dir: Path) -> list[dict[str, Any]]:
    """Writes the `virtual-bench` sample out with the venv's own `shal` (#384 — no
    repo checkout path involved), then plays it exactly as a buyer would."""
    shal = _venv_shal(venv_python)
    write_argv = [shal, "docs", "--sample", "virtual-bench", "--to", str(bench_dir)]
    w = subprocess.run(write_argv, stdin=subprocess.DEVNULL, capture_output=True,
                       text=True, timeout=60)
    if w.returncode != 0:
        raise RuntimeError(f"{_argv_str(write_argv)} exited {w.returncode}: {w.stderr.strip()}")

    pass_argv = [venv_python, "run_bench.py"]
    doc, ec = _run_json(venv_python, ["run_bench.py"], cwd=bench_dir)
    checks = [check_virtual_bench_pass(
        doc, ec, f"{_argv_str(write_argv)} && cd {bench_dir} && {_argv_str(pass_argv)}")]

    unplug_argv = [venv_python, "run_bench.py", "--unplug", "dmm"]
    env = dict(os.environ, SHAL_SIM_UNPLUG="dmm")
    proc = subprocess.run(unplug_argv, cwd=bench_dir, env=env,
                          capture_output=True, text=True, timeout=120)
    try:
        doc2 = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc2 = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    checks.append(check_virtual_bench_unplug_dmm(
        doc2, proc.returncode,
        f"{_argv_str(write_argv)} && cd {bench_dir} && {_argv_str(unplug_argv)}"))
    return checks


def run_arena_task_score_file(venv_python: str, level: str, state_dir: Path, *,
                              seed: int | None = None) -> dict[str, Any]:
    """issue #403: play the task for real -- power the card, take several
    real measurements with the packaged reference driver, diagnose from
    those readings (same pattern `shal_arena.demo`'s D5 story already
    plays, #343/#410, extended to multiple reads so `noise` is actually
    detectable, CTO review round 2) -- instead of answering `ok` blind,
    which left every cell showing `turns: 0, faults_caught: 0` regardless
    of whether the real fault was ever found. `seed` overrides the task's
    own default (used by the seed-sweep test below)."""
    task_path = ARENA_TASKS_DIR / f"{level}.yaml"
    task_doc = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    card_path = (task_path.parent / task_doc["card"]).resolve()
    card_doc = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    drives_inst = next(i for i in task_doc["instruments"] if "drives" in i)
    probe_inst = next(i for i in task_doc["instruments"] if "probe" in i)
    vin_name = drives_inst["drives"].split(".", 1)[1]
    vin_nominal_v = card_doc["inputs"][vin_name]["nominal_v"]
    allowed = set(task_doc["question"]["answer"]["values"])

    run_argv = ["-m", "shal_arena.cli", "run", str(task_path),
               "--state-dir", str(state_dir), "--json"]
    if seed is not None:
        run_argv = [*run_argv, "--seed", str(seed)]
    run_doc, _ = _run_json(venv_python, run_argv)
    run_id = run_doc["run_id"]
    rail = run_doc["rails"][0]      # issue #428: nominal_v/tol_pct, right in `run`'s own JSON

    drive_argv = ["-m", "shal_arena.cli", "drive", run_id, str(drives_inst["address"]),
                 str(vin_nominal_v), "--state-dir", str(state_dir), "--json"]
    drive_doc, drive_ec = _run_json(venv_python, drive_argv)
    # CTO review on #403 round 2: driving at the card's OWN documented
    # nominal input voltage must never be refused by SHAL's gate -- if it
    # is, the card is unpowered for every later measurement, and silently
    # continuing would misdiagnose that as a reading rather than a setup
    # failure.
    if drive_ec != EXIT_PASS:
        raise RuntimeError(
            f"driving {drives_inst['address']} to the card's own nominal {vin_nominal_v} V "
            f"failed (exit {drive_ec}): {drive_doc}")

    state_dir.mkdir(parents=True, exist_ok=True)
    with _reference_dmm_driver_path() as driver_path:
        measure_argv = ["-m", "shal_arena.cli", "measure", run_id, str(probe_inst["address"]),
                       str(driver_path), "--state-dir", str(state_dir), "--json"]
        readings: list[float] = []
        current_a: float | None = None
        for _ in range(_N_READS):
            measure_doc, measure_ec = _run_json(venv_python, measure_argv)
            if measure_ec != EXIT_PASS:
                readings = []   # a broken link to the bench, not a card fault (#477)
                break
            readings.append(measure_doc["reading"])
            current_a = measure_doc.get("current_a")

    given = _diagnose(rail["nominal_v"], rail["tol_pct"], readings, allowed, current_a)
    if given == "error":
        # issue #477: nothing to answer -- the run is an `error`, never
        # scored as a correct `fail`
        return _result(f"arena_{level}_score_file", False, json.dumps(
            {"verdict": "error", "cause": _measure_failure_cause(measure_doc),
             "measure": measure_doc}),
            f"{venv_python} {_argv_str(measure_argv)}")
    answer_argv = ["-m", "shal_arena.cli", "answer", run_id, given,
                  "--state-dir", str(state_dir), "--json"]
    answer_doc, _ = _run_json(venv_python, answer_argv)
    score_path = state_dir / f"{run_id}.score.json"
    rerun = (f"{venv_python} {_argv_str(run_argv)} && "
            f"{venv_python} {_argv_str(drive_argv)} && "
            f"{venv_python} {_argv_str(measure_argv)} && "
            f"{venv_python} {_argv_str(answer_argv)}")
    return check_arena_score_file(level, score_path.is_file(), answer_doc, rerun)


def run_arena_bench_destroyed(venv_python: str, state_dir: Path) -> dict[str, Any]:
    task_path = ARENA_TASKS_DIR / "rail-3v3.yaml"
    policy_path = state_dir / "story_bench_policy.py"
    state_dir.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(_BENCH_POLICY, encoding="utf-8")
    bench_argv = ["-m", "shal_arena.cli", "bench", str(task_path), "--runs", "10",
                 "--policy", str(policy_path), "--state-dir", str(state_dir / "bench"), "--json"]
    doc, _ = _run_json(venv_python, bench_argv)
    return check_arena_bench_destroyed(doc, f"{venv_python} {_argv_str(bench_argv)}")


def run_arena_demo(venv_python: str) -> dict[str, Any]:
    demo_argv = ["-m", "shal_arena.cli", "demo", "--pause", "0", "--json"]
    doc, ec = _run_json(venv_python, demo_argv)
    record_exists = bool(doc.get("record_path")) and Path(doc["record_path"]).is_file()
    card_exists = bool(doc.get("card_path")) and Path(doc["card_path"]).is_file()
    return check_arena_demo(doc, ec, record_exists, card_exists,
                            f"{venv_python} {_argv_str(demo_argv)}")


def _current_challenge() -> tuple[Path, int, list[dict[str, Any]]]:
    """The newest published ``arena/challenges/<week>.yaml``: its task's
    path, its seed, and that task's card ``faults:`` list."""
    path = sorted((REPO_ROOT / "arena" / "challenges").glob("*.yaml"))[-1]
    challenge = yaml.safe_load(path.read_text("utf-8"))
    task_path = ARENA_TASKS_DIR / f"{challenge['task_id']}.yaml"
    task = yaml.safe_load(task_path.read_text("utf-8"))
    card = yaml.safe_load((task_path.parent / task["card"]).read_text("utf-8"))
    return task_path, int(challenge["seed"]), card["faults"]


def run_arena_fault_determinism(venv_python: str) -> dict[str, Any]:
    task_path, seed, card_faults = _current_challenge()
    code = (
        "import json, sys\n"
        "from shal_arena.loader import load_task\n"
        "from shal_arena.fault import realized_fault\n"
        "loaded = load_task(sys.argv[1])\n"
        "rf = realized_fault(loaded.card, int(sys.argv[2]))\n"
        "print(json.dumps({'fault_id': rf.fault_id, 'extra': rf.extra}))\n"
    )
    argv = [venv_python, "-c", code, str(task_path), str(seed)]
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    rerun = (f"{venv_python} -c \"from shal_arena.loader import load_task; "
            f"from shal_arena.fault import realized_fault; "
            f"print(realized_fault(load_task('{task_path}').card, {seed}))\"")
    return check_arena_fault_determinism(doc, card_faults, seed, rerun)


def run_bricks_wheel_check(venv_python: str) -> dict[str, Any]:
    bricks_argv = [venv_python, "-c", "import bricks; print(bricks.__version__)"]
    proc = subprocess.run(bricks_argv, capture_output=True, text=True, timeout=60)
    ok = proc.returncode == 0
    version = proc.stdout.strip() if ok else None
    return check_wheel_installed("bricks-engine", version, ok, proc.stdout + proc.stderr,
                                 _argv_str(bricks_argv))


REPEATS = 20
SEED_BENCH = 7

_SHAL = ["-m", "shal.cli"]
_ARENA = ["-m", "shal_arena.cli"]

#: issue #341: (path id, which CLI, its arguments, extra env). Every path is a
#: real mistake a test engineer makes; every argument is relative to the
#: working dir `run_breaking_paths` sets up, so the committed outputs carry no
#: machine-specific path in `argv`.
BREAKING_PATHS: list[tuple[str, list[str], list[str], dict[str, str]]] = [
    # a driver op that does not exist
    ("bad_op", _SHAL, ["call", "bench.yaml", "dmm", "no_such_op", "--json"], {}),
    # a rack (topology) file naming a bus that nothing provides
    ("wrong_rack", _SHAL, ["tools", "wrong-rack.yaml", "--json"], {}),
    # 30 V requested on a rail whose limit is 3.6 V (set_voltage is a gated op)
    ("over_voltage", _SHAL, ["call", "bench.yaml", "psu", "set_voltage", "30", "--json"], {}),
    # a task file that tries to run code, through a YAML python tag
    ("trap_code", _ARENA, ["run", "trap-code.yaml", "--state-dir", "state", "--json"], {}),
    # a task file that points its card at a file outside the task folder
    ("trap_file", _ARENA, ["run", "trap-file.yaml", "--state-dir", "state", "--json"], {}),
    # the instrument's link is down (the sim's unplugged cable; the closest
    # offline stand-in for "no network": every hop to the node fails undelivered)
    ("no_network", _SHAL, ["call", "bench.yaml", "dmm", "measure_voltage", "--json"],
     {"SHAL_SIM_UNPLUG": "dmm"}),
]

_WRONG_RACK = """\
shal_version: 1
root:
  bench:
    driver: shal,no-such-bus
    address: sim0
"""


def run_breaking_paths(venv_python: str, bench_dir: Path, work_dir: Path,
                       out_dir: Path | None = None) -> list[dict[str, Any]]:
    """Runs every `BREAKING_PATHS` entry through the real CLI with ``--json``
    and checks it (`check_breaking_path`). With ``out_dir``, also writes each
    path's real output to ``<out_dir>/<id>.json`` -- the files
    ``tests/test_e2e_breaking.py`` is checked against."""
    work_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(bench_dir / "bench.yaml", work_dir / "bench.yaml")
    (work_dir / "wrong-rack.yaml").write_text(_WRONG_RACK, encoding="utf-8")
    easy = (ARENA_TASKS_DIR / "easy.yaml").read_text(encoding="utf-8")
    trap_title = 'title: !!python/object/apply:os.system ["echo pwned"]'
    (work_dir / "trap-code.yaml").write_text(
        easy.replace('title: "Is the 3V3 rail healthy?"', trap_title), encoding="utf-8")
    (work_dir / "trap-file.yaml").write_text(
        easy.replace("card: ../cards/ldo-5v-3v3.yaml", "card: /etc/passwd"), encoding="utf-8")

    checks = []
    for path_id, module, args, extra_env in BREAKING_PATHS:
        proc = subprocess.run([venv_python, *module, *args], cwd=work_dir,
                              env=dict(os.environ, **extra_env), stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=120)
        try:
            doc = json.loads(proc.stdout)
        except json.JSONDecodeError:
            doc = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r}"}
        shown = ["python", *module, *args]
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{path_id}.json").write_text(json.dumps(
                {"path": path_id, "argv": shown, "env": extra_env,
                 "exit_code": proc.returncode, "stdout": doc, "stderr": proc.stderr},
                indent=2) + "\n", encoding="utf-8")
        env_prefix = "".join(f"{k}={v} " for k, v in extra_env.items())
        checks.append(check_breaking_path(
            path_id, doc, proc.returncode, proc.stderr, f"{env_prefix}{_argv_str(shown)}"))
    return checks


def run_virtual_bench_repeat(venv_python: str, bench_dir: Path, runs: int = REPEATS
                             ) -> dict[str, Any]:
    """issue #341: the same virtual-bench story, ``runs`` times in a row, in
    this one job; the result must never change."""
    docs, codes = [], []
    for _ in range(runs):
        doc, ec = _run_json(venv_python, ["run_bench.py"], cwd=bench_dir)
        docs.append(doc)
        codes.append(ec)
    return check_repeat_identical(
        docs, codes, runs, f"cd {bench_dir} && {venv_python} run_bench.py  (x{runs})")


def run_arena_seed_summary(venv_python: str, state_dir: Path,
                           seed: int = SEED_BENCH) -> dict[str, Any]:
    argv = [*_ARENA, "bench", "--seed", str(seed), "--runs", "10",
            "--state-dir", str(state_dir), "--json"]
    doc, ec = _run_json(venv_python, argv)
    return check_seed_summary(doc, ec, seed, f"{venv_python} {_argv_str(argv)}")


def compare_seed_main(paths: list[str]) -> int:
    """`story.py compare-seed evidence.json ...`: the final job's assertion."""
    evidences = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    ok, message = compare_seed_evidence(evidences)
    print(("OK: " if ok else "FAILED: ") + message, file=None if ok else sys.stderr)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "compare-seed":
        return compare_seed_main(raw[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--venv-python", required=True,
                        help="the clean venv's python, already holding only the "
                             "release-candidate wheels")
    parser.add_argument("--versions", required=True, metavar="PATH",
                        help="JSON file: {package: {version, sha, repo, filename, sha256}}, "
                             "one entry per rc-manifest.json the wheel-gathering step read")
    parser.add_argument("--os", required=True, dest="os_label")
    parser.add_argument("--python", required=True, dest="python_label")
    parser.add_argument("--out", required=True, metavar="PATH", help="where to write evidence.json")
    parser.add_argument("--state-dir", default=None,
                        help="scratch dir for arena run state (default: a temp dir)")
    parser.add_argument("--breaking-out", default=None, metavar="DIR",
                        help="also write each breaking path's real output to DIR/<id>.json")
    parser.add_argument("--repeats", type=int, default=REPEATS,
                        help=f"how many times to repeat the virtual bench (default {REPEATS})")
    args = parser.parse_args(raw)

    versions = json.loads(Path(args.versions).read_text(encoding="utf-8"))
    state_dir = (Path(args.state_dir) if args.state_dir
                else Path(tempfile.mkdtemp(prefix="e2e-arena-")))
    out_path = Path(args.out)
    # CTO review on #380: the evidence page can't see a retry without these --
    # GitHub Actions sets both on every job, no workflow change needed.
    run_id = os.environ.get("GITHUB_RUN_ID", "unknown")
    run_attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "unknown")

    checks: list[dict[str, Any]] = []

    def run_step(step_label: str, rerun: str, fn, *fargs: Any) -> None:
        # CTO review on #340: one crash must not lose every OTHER check's
        # evidence. A step that raises is recorded as its own failed check
        # (never silently dropped), and evidence.json is rewritten after
        # EVERY step -- so even a hard crash partway through this function
        # leaves the file holding everything decided up to that point, for
        # the workflow's `if: always()` upload to pick up. `rerun` here is
        # the best rerun hint available BEFORE fn runs -- a check_* function
        # that gets to run normally supplies its own, more precise one.
        try:
            result = fn(*fargs)
        except Exception as e:  # noqa: BLE001 - must not lose the other steps' evidence
            checks.append(_result(step_label, False, f"{type(e).__name__}: {e}", rerun))
        else:
            checks.extend(result) if isinstance(result, list) else checks.append(result)
        evidence = {"os": args.os_label, "python": args.python_label, "run_id": run_id,
                   "run_attempt": run_attempt, "versions": versions, "checks": checks}
        out_path.write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    venv_py = args.venv_python
    bench_dir = state_dir / "virtual-bench"
    run_step("virtual_bench",
             f"shal docs --sample virtual-bench --to {bench_dir} && "
             f"cd {bench_dir} && {venv_py} run_bench.py",
             run_virtual_bench_checks, venv_py, bench_dir)
    for level in ARENA_TASK_LEVELS:
        run_step(f"arena_{level}_score_file",
                 f"{venv_py} -m shal_arena.cli run {ARENA_TASKS_DIR / (level + '.yaml')} "
                 f"--state-dir {state_dir / level} --json",
                 run_arena_task_score_file, venv_py, level, state_dir / level)
    run_step("arena_bench_destroyed",
             f"{venv_py} -m shal_arena.cli bench {ARENA_TASKS_DIR / 'rail-3v3.yaml'} "
             f"--runs 10 --policy <state-dir>/story_bench_policy.py --json",
             run_arena_bench_destroyed, venv_py, state_dir / "bench-story")
    run_step("arena_demo", f"{venv_py} -m shal_arena.cli demo --pause 0 --json",
             run_arena_demo, venv_py)
    run_step("arena_fault_determinism",
             f"{venv_py} -c \"from shal_arena.fault import realized_fault\"",
             run_arena_fault_determinism, venv_py)
    run_step("virtual_bench_repeat", f"cd {bench_dir} && {venv_py} run_bench.py",
             run_virtual_bench_repeat, venv_py, bench_dir, args.repeats)
    run_step("breaking_paths", f"{venv_py} -m shal.cli call bench.yaml dmm no_such_op --json",
             run_breaking_paths, venv_py, bench_dir, state_dir / "breaking",
             Path(args.breaking_out) if args.breaking_out else None)
    run_step(f"arena_bench_seed{SEED_BENCH}",
             f"{venv_py} -m shal_arena.cli bench --seed {SEED_BENCH} --runs 10 --json",
             run_arena_seed_summary, venv_py, state_dir / "bench-seed")
    run_step("wheel_installed_bricks-engine",
             f'{venv_py} -c "import bricks; print(bricks.__version__)"',
             run_bricks_wheel_check, venv_py)

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    if failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        return 1
    print(f"all {len(checks)} checks passed; evidence written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
