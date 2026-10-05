"""The batch runner (issue #314): the same task, seed and agent, with and
without SHAL, at least `MIN_RUNS` times per side — a median and a range, never
one run (the world is deterministic, the agent is not).

An agent is any callable ``agent(env)``; ``env.side`` is ``"shal"`` or
``"raw"``, so the same model plays both sides. A SHAL env offers `check`,
`measure`, `drive`; a raw env offers `send` (one raw SCPI command). Both offer
`answer`, which is not a turn. Turns are counted by the same `RunStore.
increment_turns` on both sides, and each run keeps its own sim log.

A run succeeds when its answer is correct and the card was not destroyed.
Nothing is filtered: every run is in the output, and `comparison` names the
better side per metric, including "raw" when it wins.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import statistics
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import runner
from ..errors import ArenaError
from ..store import RunStore
from .raw import RawScpi

MIN_RUNS = 10
SIDES = ("shal", "raw")


class BenchError(ArenaError):
    """A benchmark request that cannot run (too few runs, a bad agent spec)."""

    exit_code = 2


class _Env:
    side = ""

    def __init__(self, run_id: str, task: dict[str, Any], state_dir: Path) -> None:
        self.run_id = run_id
        self.task = task
        self.state_dir = state_dir
        self.answered = False

    def answer(self, value: str) -> dict[str, Any]:
        result = runner.answer(self.run_id, value, state_dir=self.state_dir)
        self.answered = True
        return result


class ShalEnv(_Env):
    side = "shal"

    def check(self, address: str, driver_path: str | Path) -> dict[str, Any]:
        return runner.check_instrument_driver(self.run_id, address, driver_path,
                                              state_dir=self.state_dir)

    def measure(self, address: str, driver_path: str | Path) -> dict[str, Any]:
        return runner.take_measurement(self.run_id, address, driver_path,
                                       state_dir=self.state_dir)

    def drive(self, address: str, volts: float) -> dict[str, Any]:
        return runner.drive_input(self.run_id, address, volts, state_dir=self.state_dir)


class RawEnv(_Env):
    side = "raw"

    def __init__(self, run_id: str, task: dict[str, Any], state_dir: Path) -> None:
        super().__init__(run_id, task, state_dir)
        self._scpi = RawScpi(run_id, state_dir=state_dir)

    def send(self, address: str, cmd: str) -> str:
        return self._scpi.send(address, cmd)


Agent = Callable[[_Env], None]


def load_agent(spec: str) -> Agent:
    """``module:attr`` or ``path/to/file.py:attr`` -> the agent callable."""
    mod_name, sep, attr = spec.rpartition(":")
    if not sep or not mod_name or not attr:
        raise BenchError(f"agent {spec!r} is not module:attr",
                         fix="pass --agent my_pkg.my_module:my_agent or --agent ./agent.py:run")
    try:
        if mod_name.endswith(".py"):
            mspec = importlib.util.spec_from_file_location(Path(mod_name).stem, mod_name)
            module = importlib.util.module_from_spec(mspec)
            mspec.loader.exec_module(module)
        else:
            module = importlib.import_module(mod_name)
        return getattr(module, attr)
    except (ImportError, AttributeError, OSError) as e:
        raise BenchError(f"could not load agent {spec!r}: {type(e).__name__}: {e}",
                         fix="check the module path and that it defines the named callable") from e


def _spread(values: list[float]) -> dict[str, float]:
    return {"median": statistics.median(values), "min": min(values), "max": max(values)}


def _run_once(side: str, agent: Agent, task_path: str, seed: int | None,
              state_dir: Path) -> dict[str, Any]:
    started = runner.start_run(task_path, seed=seed, state_dir=state_dir)
    run_id = started["run_id"]
    env_cls = ShalEnv if side == "shal" else RawEnv
    env = env_cls(run_id, started, state_dir)
    t0 = time.monotonic()
    error = None
    try:
        agent(env)
    except Exception as e:  # noqa: BLE001 - an agent crash is a recorded failed run, not hidden
        error = f"{type(e).__name__}: {e}"
    seconds = time.monotonic() - t0
    store = RunStore(state_dir)
    state = store.load(run_id)
    correct = False
    if env.answered:
        correct = bool(_load_record(store, run_id)["correct"])
    return {"run_id": run_id, "answered": env.answered, "correct": correct,
            "destroyed": state.card_destroyed,
            "success": correct and not state.card_destroyed,
            "turns": state.turns, "seconds": round(seconds, 4), "error": error,
            "sim_log": str(store.sim_log_path(run_id))}


def _load_record(store: RunStore, run_id: str) -> dict[str, Any]:
    return json.loads(store.record_path(run_id).read_text(encoding="utf-8"))


def _summarise(runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "runs": runs,
        "turns": _spread([r["turns"] for r in runs]),
        "seconds": _spread([r["seconds"] for r in runs]),
        "success_rate": sum(r["success"] for r in runs) / len(runs),
        "destroyed": sum(r["destroyed"] for r in runs),
        "sim_logs": [r["sim_log"] for r in runs],
    }


def _better(shal: float, raw: float, *, higher_is_better: bool) -> str:
    if shal == raw:
        return "tie"
    return "shal" if (shal > raw) == higher_is_better else "raw"


def run_bench(task_path: str, agent: Agent, *, runs: int = MIN_RUNS, seed: int | None = None,
              state_dir: str | Path = ".shal-arena") -> dict[str, Any]:
    """Run ``agent`` ``runs`` times on each side of ``task_path`` (same seed)
    and report median and range per side."""
    if runs < MIN_RUNS:
        raise BenchError(
            f"--runs {runs} is below the minimum of {MIN_RUNS}: one run is noise, "
            "the agent is not deterministic",
            fix=f"pass --runs {MIN_RUNS} or more")
    root = Path(state_dir) / "bench"
    sides = {side: _summarise([_run_once(side, agent, task_path, seed, root / side)
                               for _ in range(runs)]) for side in SIDES}
    s, r = sides["shal"], sides["raw"]
    return {
        "ok": True,
        "side_effect": "write",
        "task": str(task_path),
        "runs_per_side": runs,
        "sides": sides,
        "comparison": {
            "turns": _better(s["turns"]["median"], r["turns"]["median"], higher_is_better=False),
            "success_rate": _better(s["success_rate"], r["success_rate"], higher_is_better=True),
            "destroyed": _better(s["destroyed"], r["destroyed"], higher_is_better=False),
        },
    }
