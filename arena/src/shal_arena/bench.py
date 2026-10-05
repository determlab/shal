"""Benchmark mode (issue #314): the same task played with SHAL (`start_run` +
`check_instrument_driver` + `take_measurement` + `drive_input` + `answer`,
the existing runner API, unmodified) and without SHAL (`start_run` +
`raw_scpi` + `answer` — no driver.py, no gate, no record) on the identical
seeded world, so the two sides are comparable.

What "comparable" rests on, concretely:

- The SAME `CardSim`/sim-bus mechanics underlie both sides (`runner.py`'s own
  docstring) — this module never re-implements any of that, only plays it.
- One turn = one call that reaches the sim on either side (`check`, `measure`,
  `drive`, or a raw SCPI command); `answer` is never a turn — the CTO's
  turn-counting ruling (issue #325/#314) this ticket's own tests pin end to
  end, via `run_benchmark`'s per-side turn counts.
- At least `MIN_RUNS` runs per side, because one run says nothing about a
  seed-sensitive world — `run_benchmark` refuses fewer, naming the fix.

Who decides what to measure/drive/answer on each run — the actual policy
under test — is the caller's job, not this module's: running a real model
across many tasks and publishing numbers is explicitly out of scope for this
ticket. `run_benchmark` takes two plain callables (`play_with_shal` /
`play_without_shal`, each ``(task_path, seed, state_dir) -> (run_id,
closing_record)``) so a scripted sequence (tests; no model needed) and a real
agent (a later ticket) are the same shape of caller. `shal-arena bench`'s
``--policy`` is how an agent supplies its own ("any agent can play", the
arena-wide ruling in issue #310's Context — one vendor's SDK is not
privileged here either).

Results always report both sides, unconditionally — a task where "without
SHAL" does better is not filtered out (Scope: "nothing is hidden")."""
from __future__ import annotations

import importlib.util
import statistics
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from .errors import ArenaError, TooFewRuns
from .store import DEFAULT_STATE_DIR, RunStore

MIN_RUNS = 10

# (task_path, seed, state_dir) -> (run_id, the dict `runner.answer` returned)
Play = Callable[[str, int, str], tuple[str, dict[str, Any]]]


class _Policy(Protocol):
    def play_with_shal(self, task_path: str, seed: int, state_dir: str
                       ) -> tuple[str, dict[str, Any]]: ...

    def play_without_shal(self, task_path: str, seed: int, state_dir: str
                          ) -> tuple[str, dict[str, Any]]: ...


def import_policy(path: str | Path) -> _Policy:
    """Load a ``--policy`` file the same dynamic way the CLI already loads a
    player's ``driver.py`` (`runner._import_driver_file`) — a plain Python
    module defining ``play_with_shal``/``play_without_shal``, owned by
    whoever is running the benchmark, never by `shal_arena` itself."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ArenaError(f"policy file not found: {path}",
                         fix="pass the path to a policy.py defining play_with_shal "
                             "and play_without_shal")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    for name in ("play_with_shal", "play_without_shal"):
        if not callable(getattr(module, name, None)):
            raise ArenaError(f"{path}: does not define a callable {name}()",
                             fix=f"add a top-level function {name}(task_path, seed, "
                                 "state_dir) -> (run_id, answer_record) to the policy file")
    return module


def run_side(task_path: str, play: Play, *, runs: int, seed_base: int,
            state_dir: str | Path) -> list[dict[str, Any]]:
    """Play ``runs`` runs of one side through ``play``, one per seed
    (``seed_base + i``) so each run samples a (likely) different realized
    fault — a single run says nothing about a seed-sensitive world, which is
    exactly why `run_benchmark` refuses fewer than `MIN_RUNS` of these."""
    store = RunStore(state_dir)
    results = []
    for i in range(runs):
        run_id, record = play(str(task_path), seed_base + i, str(state_dir))
        state = store.load(run_id)
        # a destroyed card is a failed run on its own terms (Scope: "30 V on
        # a 5 V card destroys it and the task fails"), whatever `given`
        # happened to match — CTO review on #328: counting it "correct"
        # because the answer also named the right fault hid the damage.
        destroyed = state.card_destroyed
        correct = bool(record.get("correct")) and not destroyed
        results.append({"run_id": run_id, "seed": seed_base + i, "turns": state.turns,
                        "correct": correct, "disqualified": bool(record.get("disqualified")),
                        "destroyed": destroyed,
                        "sim_log": str(store.sim_log_path(run_id))})
    return results


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Median and range of turns across ``results`` — the metric the CTO's
    turn-counting ruling makes comparable between the two sides at all —
    plus how many runs were disqualified or destroyed the card (CTO review
    on #328: a side's score is not just its turns and correctness) and the
    sim log path for every run, so anyone can check what each run sent."""
    turns = [r["turns"] for r in results]
    return {
        "runs": len(results),
        "median_turns": statistics.median(turns),
        "turns_range": [min(turns), max(turns)],
        "correct": sum(1 for r in results if r["correct"]),
        "disqualified": sum(1 for r in results if r["disqualified"]),
        "destroyed": sum(1 for r in results if r["destroyed"]),
        "sim_logs": [r["sim_log"] for r in results],
    }


def run_benchmark(task_path: str, *, play_with_shal: Play, play_without_shal: Play,
                  runs: int, seed_base: int = 0,
                  state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Play ``runs`` runs per side and report both sides' median/range of
    turns, unconditionally (Scope: "nothing is hidden"). Refuses ``runs <
    MIN_RUNS`` before either side plays anything (Scope: "at least 10 runs
    per side")."""
    if runs < MIN_RUNS:
        raise TooFewRuns(
            f"--runs {runs} is below the minimum of {MIN_RUNS} runs per side",
            fix=f"pass --runs {MIN_RUNS} or higher")
    base = Path(state_dir)
    with_results = run_side(task_path, play_with_shal, runs=runs, seed_base=seed_base,
                            state_dir=base / "with_shal")
    without_results = run_side(task_path, play_without_shal, runs=runs, seed_base=seed_base,
                               state_dir=base / "without_shal")
    return {
        "ok": True,
        "side_effect": "write",   # writes two state dirs, `runs` runs each
        "task": str(task_path),
        "runs": runs,
        "with_shal": summarize(with_results),
        "without_shal": summarize(without_results),
    }
