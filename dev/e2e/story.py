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
(``examples/demos/virtual-bench``) and SHAL Arena (``arena/``) — entirely
through that venv's own ``python``, and writes one ``evidence.json``: which
OS, which Python, which commit of each repo, and pass/fail for every check.

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
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
VIRTUAL_BENCH_DIR = REPO_ROOT / "examples" / "demos" / "virtual-bench"
ARENA_TASKS_DIR = REPO_ROOT / "arena" / "src" / "shal_arena" / "tasks"
ARENA_TASK_LEVELS = ["easy", "medium", "hard"]

# shal#300's Unreachable exit code; run_bench.py's own vocabulary
# (examples/demos/virtual-bench/run_bench.py).
EXIT_PASS = 0
EXIT_UNREACHABLE = 4

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


def _result(check_id: str, ok: bool, log: str) -> dict[str, Any]:
    return {"id": check_id, "result": "pass" if ok else "fail", "log": log}


# -- pure checks (Done-when: "every check reads JSON output, never text") --


def check_virtual_bench_pass(doc: dict[str, Any], exit_code: int) -> dict[str, Any]:
    ok = doc.get("verdict") == "pass" and exit_code == EXIT_PASS
    return _result("virtual_bench_pass", ok, json.dumps(doc))


def check_virtual_bench_unplug_dmm(doc: dict[str, Any], exit_code: int) -> dict[str, Any]:
    ok = (doc.get("verdict") == "error" and doc.get("cause") == "transport"
         and exit_code != EXIT_PASS and exit_code == EXIT_UNREACHABLE)
    return _result("virtual_bench_unplug_dmm", ok, json.dumps(doc))


def check_arena_score_file(level: str, score_exists: bool,
                           answer_doc: dict[str, Any]) -> dict[str, Any]:
    ok = score_exists and bool(answer_doc.get("score"))
    return _result(f"arena_{level}_score_file", ok, json.dumps(
        {"score_file_exists": score_exists, "score": answer_doc.get("score")}))


def check_arena_bench_destroyed(doc: dict[str, Any]) -> dict[str, Any]:
    with_destroyed = doc.get("with_shal", {}).get("destroyed")
    without_destroyed = doc.get("without_shal", {}).get("destroyed")
    ok = with_destroyed == 0 and isinstance(without_destroyed, int) and without_destroyed > 0
    return _result("arena_bench_destroyed", ok, json.dumps(
        {"with_shal_destroyed": with_destroyed, "without_shal_destroyed": without_destroyed}))


def check_wheel_installed(package: str, version: str | None, ok: bool,
                          output: str) -> dict[str, Any]:
    return _result(f"wheel_installed_{package}", ok, f"version={version} output={output!r}")


# -- the real world: runs through the clean venv's own python -------------- #


def _run_json(venv_python: str, argv: list[str], cwd: Path | None = None) -> tuple[dict, int]:
    proc = subprocess.run([venv_python, *argv], cwd=cwd, capture_output=True, text=True,
                          timeout=120)
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    return doc, proc.returncode


def run_virtual_bench_checks(venv_python: str) -> list[dict[str, Any]]:
    doc, ec = _run_json(venv_python, ["run_bench.py"], cwd=VIRTUAL_BENCH_DIR)
    checks = [check_virtual_bench_pass(doc, ec)]
    env = dict(os.environ, SHAL_SIM_UNPLUG="dmm")
    proc = subprocess.run([venv_python, "run_bench.py", "--unplug", "dmm"],
                          cwd=VIRTUAL_BENCH_DIR, env=env, capture_output=True, text=True,
                          timeout=120)
    try:
        doc2 = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc2 = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    checks.append(check_virtual_bench_unplug_dmm(doc2, proc.returncode))
    return checks


def run_arena_task_score_file(venv_python: str, level: str, state_dir: Path) -> dict[str, Any]:
    task_path = ARENA_TASKS_DIR / f"{level}.yaml"
    run_doc, _ = _run_json(venv_python,
                           ["-m", "shal_arena.cli", "run", str(task_path),
                            "--state-dir", str(state_dir), "--json"])
    run_id = run_doc["run_id"]
    answer_doc, _ = _run_json(venv_python,
                              ["-m", "shal_arena.cli", "answer", run_id, "ok",
                               "--state-dir", str(state_dir), "--json"])
    score_path = state_dir / f"{run_id}.score.json"
    return check_arena_score_file(level, score_path.is_file(), answer_doc)


def run_arena_bench_destroyed(venv_python: str, state_dir: Path) -> dict[str, Any]:
    task_path = ARENA_TASKS_DIR / "rail-3v3.yaml"
    policy_path = state_dir / "story_bench_policy.py"
    state_dir.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(_BENCH_POLICY, encoding="utf-8")
    doc, _ = _run_json(venv_python,
                       ["-m", "shal_arena.cli", "bench", str(task_path), "--runs", "10",
                        "--policy", str(policy_path), "--state-dir", str(state_dir / "bench"),
                        "--json"])
    return check_arena_bench_destroyed(doc)


def run_bricks_wheel_check(venv_python: str) -> dict[str, Any]:
    proc = subprocess.run(
        [venv_python, "-c", "import bricks; print(bricks.__version__)"],
        capture_output=True, text=True, timeout=60)
    ok = proc.returncode == 0
    version = proc.stdout.strip() if ok else None
    return check_wheel_installed("bricks-engine", version, ok, proc.stdout + proc.stderr)


def main(argv: list[str] | None = None) -> int:
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
    args = parser.parse_args(argv)

    versions = json.loads(Path(args.versions).read_text(encoding="utf-8"))
    state_dir = (Path(args.state_dir) if args.state_dir
                else Path(tempfile.mkdtemp(prefix="e2e-arena-")))

    checks: list[dict[str, Any]] = []
    checks += run_virtual_bench_checks(args.venv_python)
    for level in ARENA_TASK_LEVELS:
        checks.append(run_arena_task_score_file(args.venv_python, level, state_dir / level))
    checks.append(run_arena_bench_destroyed(args.venv_python, state_dir / "bench-story"))
    checks.append(run_bricks_wheel_check(args.venv_python))

    evidence = {"os": args.os_label, "python": args.python_label, "versions": versions,
               "checks": checks}
    Path(args.out).write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    failed = [c["id"] for c in checks if c["result"] != "pass"]
    if failed:
        print(f"FAILED: {failed}", file=sys.stderr)
        return 1
    print(f"all {len(checks)} checks passed; evidence written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
