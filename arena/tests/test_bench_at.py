"""issue #498: `shal-arena bench` and `shal-arena demo` on power-28v -- a DMM
moved between three rails (`probes:`) -- pass `--at` (`at=`) once per rail,
on both bench sides, and a task without `probes:` still plays exactly as it
did before (no `at`, so no point in its log)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena import demo
from shal_arena import fault as fault_mod
from shal_arena.bench import (
    DEFAULT_POLICY,
    _default_dmm_driver_registered,
    answer_from_points,
    rail_points,
    run_benchmark,
)
from shal_arena.loader import load_task, resolve_task
from shal_arena.simlog import SimLog
from shal_arena.store import RunStore

from .conftest import SAMPLE_TASK

TASK = str(resolve_task("power-28v"))
_POINTS = ["tp_12v", "tp_5v", "tp_3v3"]
_CARD = load_task(TASK).card


def _measured_points(state_dir: Path, run_id: str) -> list[str | None]:
    entries = SimLog(RunStore(state_dir).sim_log_path(run_id)).entries()
    return [e.get("point") for e in entries if e["kind"] == "measure" and e["address"] == "dmm0"]


def test_rail_points_names_each_rail_for_power_28v_and_none_for_one_point() -> None:
    assert rail_points(TASK) == ("dmm0", _POINTS)
    assert rail_points(str(SAMPLE_TASK)) == ("dmm0", [None])


@pytest.mark.parametrize("side", ["play_with_shal", "play_without_shal"])
def test_bench_default_policy_passes_at_per_rail(side: str, tmp_path: Path) -> None:
    with _default_dmm_driver_registered():
        run_id, record = getattr(DEFAULT_POLICY, side)(TASK, 0, str(tmp_path))
    assert _measured_points(tmp_path, run_id) == _POINTS
    assert record["disqualified"] is False


@pytest.mark.parametrize("side", ["play_with_shal", "play_without_shal"])
def test_bench_without_probes_sends_no_at(side: str, tmp_path: Path) -> None:
    with _default_dmm_driver_registered():
        run_id, _ = getattr(DEFAULT_POLICY, side)(str(SAMPLE_TASK), 0, str(tmp_path))
    assert _measured_points(tmp_path, run_id) == [None]


def test_bench_power_28v_ten_runs_measure_and_answer_on_both_sides(tmp_path: Path) -> None:
    with _default_dmm_driver_registered():
        result = run_benchmark(TASK, play_with_shal=DEFAULT_POLICY.play_with_shal,
                               play_without_shal=DEFAULT_POLICY.play_without_shal,
                               runs=10, state_dir=tmp_path)
    for side in ("with_shal", "without_shal"):
        s = result[side]
        assert s["measurement_failures"] == 0 and s["disqualified"] == 0
        assert s["destroyed"] == 0 and s["correct"] > 0
        # 1 drive + one measure per rail, the same on both sides
        assert s["turns_range"] == [1 + len(_POINTS), 1 + len(_POINTS)]


def test_bench_cli_power_28v_json(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "bench", "power-28v", "--runs", "10",
         "--state-dir", str(tmp_path), "--json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True and doc["with_shal"]["measurement_failures"] == 0


@pytest.mark.parametrize("fault_id,readings,temps", [
    ("ok", {"tp_12v": 12.0, "tp_5v": 5.0, "tp_3v3": 3.3}, {"tp_u2_temp": 45.0}),
    ("low_5v", {"tp_12v": 12.0, "tp_5v": 4.6, "tp_3v3": 3.3}, {}),
    ("open_12v", {"tp_12v": 0.0, "tp_5v": 5.0, "tp_3v3": 3.3}, {}),
    ("low_3v3", {"tp_12v": 12.0, "tp_5v": 5.0, "tp_3v3": 3.0}, {}),
    ("overheat_u2", {"tp_12v": 12.0, "tp_5v": 5.0, "tp_3v3": 3.3}, {"tp_u2_temp": 91.0}),
])
def test_answer_from_points_names_the_card_fault(fault_id: str, readings: dict,
                                                 temps: dict) -> None:
    assert answer_from_points(TASK, readings, temps) == fault_id


def _demo_ctx(tmp_path: Path) -> dict:
    dmm_driver = tmp_path / "dmm_driver.py"
    dmm_driver.write_text(demo._DMM_DRIVER_SOURCE, encoding="utf-8")
    return {"state_dir": tmp_path, "dmm_driver": dmm_driver, "arena_runs": {}}


def test_demo_power_28v_step_passes_at_per_rail_and_answers_correctly(tmp_path: Path) -> None:
    result = demo._run_power_28v(_demo_ctx(tmp_path))
    assert result["at"] == _POINTS
    assert set(result["readings"]) == set(_POINTS)
    assert _measured_points(tmp_path / "power-28v", result["run_id"]) == _POINTS
    assert demo.check_arena_task(result)
    # the task's own seed realizes this fault; the answer names it
    realized = fault_mod.realized_fault(_CARD, load_task(TASK).task.seed).fault_id
    assert result["given"] == realized


def test_demo_lists_the_power_28v_step_as_scripted() -> None:
    steps = {step_id: line for step_id, line, *_ in demo._STEPS}
    assert "arena_power_28v" in steps
    assert steps["arena_power_28v"].startswith(demo._SCRIPTED_PLAYER_NOTE)
    assert "--at" in steps["arena_power_28v"]


def test_demo_json_ends_power_28v_with_a_correct_verdict(tmp_path: Path) -> None:
    proc = subprocess.run([sys.executable, "-m", "shal_arena.cli", "demo", "--json"],
                          stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=180, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    step = next(s for s in doc["steps"] if s["step"] == "arena_power_28v")["result"]
    assert step["at"] == _POINTS
    assert step["correct"] is True and step["disqualified"] is False
