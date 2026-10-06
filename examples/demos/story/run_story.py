#!/usr/bin/env python3
"""The whole SHAL story in one script (issue #343, D5): a short pause and one
plain line before each step, so a person can watch it once.

    python examples/demos/story/run_story.py            # for a person
    python examples/demos/story/run_story.py --pause 0 --json   # for CI / an agent

Steps, in order: the virtual bench passes; the same bench with the DMM
unplugged gives ERROR; a 30 V request to a 5 V card is blocked; the arena
easy, medium and hard tasks; the arena bench, 10 runs per side.

It installs nothing. It reuses ``examples/demos/virtual-bench/`` (needs pyshal
and pytest-shal in this Python) and ``shal-arena`` (needs ``shal_arena``). In a
checkout, ``arena/src`` is put on the path when ``shal_arena`` is not
installed. Arena state goes to a temporary directory that is removed after.

``--json`` prints one document on stdout: ``{"ok", "steps": [{"step", "line",
"result", "ok"}, ...]}``. The first line printed is the notice below, on stderr
under ``--json``. Exit 0 every step did what the story says, 1 one did not,
3 the story could not run.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BENCH_DIR = ROOT / "examples" / "demos" / "virtual-bench"

NOTICE = "Simulated instruments only. Nothing here touches real hardware."

EXIT_OK = 0
EXIT_STEP_FAILED = 1
EXIT_CANNOT_RUN = 3


def _ensure_arena_importable() -> None:
    try:
        import shal_arena  # noqa: F401
    except ImportError:
        src = ROOT / "arena" / "src"
        if src.is_dir():
            sys.path.insert(0, str(src))


def _bench_step(*extra: str) -> tuple[dict, bool]:
    proc = subprocess.run([sys.executable, str(BENCH_DIR / "run_bench.py"), *extra],
                          cwd=BENCH_DIR, capture_output=True, text=True)
    try:
        summary = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError):
        summary = {"ok": False, "error": (proc.stderr or proc.stdout)[-500:]}
    return {**summary, "exit_code": proc.returncode}, proc.returncode


def step_bench_pass(_state: Path) -> tuple[dict, bool]:
    result, code = _bench_step()
    return result, code == 0 and result.get("verdict") == "pass"


def step_bench_unplug(_state: Path) -> tuple[dict, bool]:
    result, code = _bench_step("--unplug", "dmm")
    return result, code == 4 and result.get("verdict") == "error"


def _task(level: str) -> str:
    import shal_arena
    return str(Path(shal_arena.__file__).resolve().parent / "tasks" / f"{level}.yaml")


def step_blocked_30v(state: Path) -> tuple[dict, bool]:
    from shal_arena.runner import drive_input, start_run
    run_id = start_run(_task("easy"), state_dir=state)["run_id"]
    out = drive_input(run_id, "psu0", 30.0, state_dir=state)
    result = {"volts": 30.0, "sent": out["sent"], "rejected": out.get("rejected"),
              "card_state": out["state"]}
    return result, out["sent"] is False


def _arena_level(level: str) -> Callable[[Path], tuple[dict, bool]]:
    def step(state: Path) -> tuple[dict, bool]:
        from shal_arena.store import RunStore
        sys.path.insert(0, str(HERE))
        import story_policy
        run_id, record = story_policy.play_with_shal(_task(level), 0, str(state))
        turns = RunStore(state).load(run_id).turns
        return ({"task": level, "run_id": run_id, "turns": turns,
                 "given": record["given"], "correct": bool(record["correct"])}, True)
    return step


def step_arena_bench(state: Path) -> tuple[dict, bool]:
    from shal_arena.bench import import_policy, run_benchmark
    policy = import_policy(HERE / "story_policy.py")
    out = run_benchmark(_task("easy"), play_with_shal=policy.play_with_shal,
                        play_without_shal=policy.play_without_shal, runs=10,
                        state_dir=state / "bench")
    result = {"task": "easy", "runs": out["runs"]}
    for side in ("with_shal", "without_shal"):
        s = out[side]
        result[side] = {"runs": s["runs"], "median_turns": s["median_turns"],
                        "turns_range": s["turns_range"], "correct": s["correct"],
                        "destroyed": s["destroyed"]}
    return result, out["with_shal"]["runs"] == 10 and out["without_shal"]["runs"] == 10


STEPS: list[tuple[str, str, Callable[[Path], tuple[dict, bool]]]] = [
    ("bench-pass", "Step 1. The virtual bench: a power supply sets 3.3 V and a meter reads it.",
     step_bench_pass),
    ("bench-unplug", "Step 2. The same bench with the meter unplugged. It should say ERROR.",
     step_bench_unplug),
    ("blocked-30v", "Step 3. Ask a 5 V card for 30 V. The gate should block it.",
     step_blocked_30v),
    ("arena-easy", "Step 4. Arena, easy task: drive the card, take one reading, answer.",
     _arena_level("easy")),
    ("arena-medium", "Step 5. Arena, medium task: the same moves on a 12 V card.",
     _arena_level("medium")),
    ("arena-hard", "Step 6. Arena, hard task: the same moves with a tighter tolerance.",
     _arena_level("hard")),
    ("arena-bench", "Step 7. Arena bench: the easy task, 10 runs with SHAL and 10 without.",
     step_arena_bench),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pause", type=float, default=1.5, metavar="SECONDS",
                        help="seconds to wait before each step (default: 1.5; 0 for CI)")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON document on stdout instead of text")
    args = parser.parse_args(argv)

    print(NOTICE, file=sys.stderr if args.json else sys.stdout, flush=True)

    _ensure_arena_importable()
    try:
        import shal_arena  # noqa: F401

        import shal  # noqa: F401
    except ImportError as e:
        err = {"type": "ImportError", "message": f"{e}",
               "fix": "pip install the release-candidate wheels of pyshal, pytest-shal "
                      "and shal-arena into this Python, then run this script again"}
        if args.json:
            print(json.dumps({"ok": False, "error": err}))
        print(f"cannot run: {err['message']}\nfix: {err['fix']}", file=sys.stderr)
        return EXIT_CANNOT_RUN

    steps: list[dict] = []
    all_ok = True
    with tempfile.TemporaryDirectory(prefix="shal-story-") as tmp:
        state = Path(tmp)
        for name, line, run in STEPS:
            if args.pause > 0:
                time.sleep(args.pause)
            if not args.json:
                print(line, flush=True)
            try:
                result, ok = run(state)
            except Exception as e:  # noqa: BLE001 - reported as the step's own result
                result, ok = {"error": f"{type(e).__name__}: {e}"}, False
            all_ok = all_ok and ok
            steps.append({"step": name, "line": line, "result": result, "ok": ok})
            if not args.json:
                print(f"  {json.dumps(result)}", flush=True)

    if args.json:
        print(json.dumps({"ok": all_ok, "side_effect": "none", "steps": steps}, indent=2))
    return EXIT_OK if all_ok else EXIT_STEP_FAILED


if __name__ == "__main__":
    sys.exit(main())
