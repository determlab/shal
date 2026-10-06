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
(``shal docs --sample virtual-bench``) and SHAL Arena (``arena/``) — entirely
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
ARENA_TASKS_DIR = REPO_ROOT / "arena" / "src" / "shal_arena" / "tasks"
ARENA_TASK_LEVELS = ["easy", "medium", "hard"]

# shal#300's Unreachable exit code; run_bench.py's own vocabulary
# (the virtual-bench sample's run_bench.py).
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
    ok = score_exists and bool(answer_doc.get("score"))
    return _result(f"arena_{level}_score_file", ok, json.dumps(
        {"score_file_exists": score_exists, "score": answer_doc.get("score")}), rerun)


def check_arena_bench_destroyed(doc: dict[str, Any], rerun: str) -> dict[str, Any]:
    with_destroyed = doc.get("with_shal", {}).get("destroyed")
    without_destroyed = doc.get("without_shal", {}).get("destroyed")
    ok = with_destroyed == 0 and isinstance(without_destroyed, int) and without_destroyed > 0
    return _result("arena_bench_destroyed", ok, json.dumps(
        {"with_shal_destroyed": with_destroyed, "without_shal_destroyed": without_destroyed}),
        rerun)


def check_wheel_installed(package: str, version: str | None, ok: bool, output: str,
                          rerun: str) -> dict[str, Any]:
    return _result(f"wheel_installed_{package}", ok, f"version={version} output={output!r}",
                   rerun)


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


_SHAL_CLI = "import sys; from shal.cli import main; sys.exit(main(sys.argv[1:]))"


def run_virtual_bench_checks(venv_python: str) -> list[dict[str, Any]]:
    # The bench comes out of the installed pyshal wheel (`shal docs --sample`), the way a
    # buyer gets it -- never from this repo's examples/ folder.
    bench_dir = Path(tempfile.mkdtemp(prefix="e2e-bench-")) / "bench"
    write_argv = [venv_python, "-c", _SHAL_CLI, "docs", "--sample", "virtual-bench",
                  "--to", str(bench_dir)]
    written = subprocess.run(write_argv, capture_output=True, text=True, timeout=120)
    if written.returncode != 0:
        raise RuntimeError(f"shal docs --sample virtual-bench --to {bench_dir} exited "
                           f"{written.returncode}: {written.stderr.strip()}")
    pass_argv = [venv_python, "run_bench.py"]
    doc, ec = _run_json(venv_python, ["run_bench.py"], cwd=bench_dir)
    checks = [check_virtual_bench_pass(
        doc, ec, f"shal docs --sample virtual-bench --to {bench_dir} && "
                 f"cd {bench_dir} && {_argv_str(pass_argv)}")]

    unplug_argv = [venv_python, "run_bench.py", "--unplug", "dmm"]
    env = dict(os.environ, SHAL_SIM_UNPLUG="dmm")
    proc = subprocess.run(unplug_argv, cwd=bench_dir, env=env,
                          capture_output=True, text=True, timeout=120)
    try:
        doc2 = json.loads(proc.stdout)
    except json.JSONDecodeError:
        doc2 = {"ok": False, "error": f"not JSON: stdout={proc.stdout!r} stderr={proc.stderr!r}"}
    checks.append(check_virtual_bench_unplug_dmm(
        doc2, proc.returncode, f"cd {bench_dir} && {_argv_str(unplug_argv)}"))
    return checks


def run_arena_task_score_file(venv_python: str, level: str, state_dir: Path) -> dict[str, Any]:
    task_path = ARENA_TASKS_DIR / f"{level}.yaml"
    run_argv = ["-m", "shal_arena.cli", "run", str(task_path),
               "--state-dir", str(state_dir), "--json"]
    run_doc, _ = _run_json(venv_python, run_argv)
    run_id = run_doc["run_id"]
    answer_argv = ["-m", "shal_arena.cli", "answer", run_id, "ok",
                  "--state-dir", str(state_dir), "--json"]
    answer_doc, _ = _run_json(venv_python, answer_argv)
    score_path = state_dir / f"{run_id}.score.json"
    rerun = (f"{venv_python} {_argv_str(run_argv)} && "
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


def run_bricks_wheel_check(venv_python: str) -> dict[str, Any]:
    bricks_argv = [venv_python, "-c", "import bricks; print(bricks.__version__)"]
    proc = subprocess.run(bricks_argv, capture_output=True, text=True, timeout=60)
    ok = proc.returncode == 0
    version = proc.stdout.strip() if ok else None
    return check_wheel_installed("bricks-engine", version, ok, proc.stdout + proc.stderr,
                                 _argv_str(bricks_argv))


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
    run_step("virtual_bench",
             f"shal docs --sample virtual-bench --to <dir> && cd <dir> && {venv_py} run_bench.py",
             run_virtual_bench_checks, venv_py)
    for level in ARENA_TASK_LEVELS:
        run_step(f"arena_{level}_score_file",
                 f"{venv_py} -m shal_arena.cli run {ARENA_TASKS_DIR / (level + '.yaml')} "
                 f"--state-dir {state_dir / level} --json",
                 run_arena_task_score_file, venv_py, level, state_dir / level)
    run_step("arena_bench_destroyed",
             f"{venv_py} -m shal_arena.cli bench {ARENA_TASKS_DIR / 'rail-3v3.yaml'} "
             f"--runs 10 --policy <state-dir>/story_bench_policy.py --json",
             run_arena_bench_destroyed, venv_py, state_dir / "bench-story")
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
