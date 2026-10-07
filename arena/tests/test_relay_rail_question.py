"""issue #461: the relay-rail question stops telling the agent where to
look. The CTO's own ruling (issue comment, 2026-10-06 22:03) keeps the
power-up line -- "Power it at 12.0 V through relay0" states the test's
operating conditions, not a hint about where the fault is -- but drops
every word naming which point to measure or which limit to check (no
3V3, no regulator, no temperature). Those stay discoverable only through
the run JSON's own `rails`/`temp_points`. No scoring or fault-draw logic
changes here; this only changes the question text and pins the healthy-
seed behaviour that already exists."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.runner import answer, start_run, take_measurement

from .conftest import PASSING_DMM_DRIVER, RELAY_RAIL_TASK

_LOADED = load_task(RELAY_RAIL_TASK)
_TASK, _CARD = _LOADED.task, _LOADED.card

_NEW_QUESTION_TEXT = ("This card came back from the line. Power it at 12.0 V "
                     "through relay0. Is it good? If not, name the fault.")


# --------------------------------------------------------------------------- #
# the question text itself
# --------------------------------------------------------------------------- #

def test_question_text_is_exactly_the_new_sentence_with_no_rail_or_limit_hint():
    assert _TASK.question.text == _NEW_QUESTION_TEXT
    for word in ("3V3", "regulator", "temperature"):
        assert word not in _TASK.question.text


def test_the_power_up_line_stays_per_the_cto_ruling():
    """The input voltage and power path are the test's own operating
    conditions, not a hint about where the fault is (CTO ruling) -- they
    stay in the question, unlike the rail/limit words above."""
    assert "12.0 V" in _TASK.question.text
    assert "relay0" in _TASK.question.text


# --------------------------------------------------------------------------- #
# the answer values are still visible in the run JSON
# --------------------------------------------------------------------------- #

def test_cli_run_still_lists_the_four_answer_values(tmp_path):
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "run", str(RELAY_RAIL_TASK),
         "--state-dir", str(tmp_path / "state"), "--json"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    assert doc["task"]["answer"]["values"] == [
        "ok", "low_voltage", "open", "overheat"]


# --------------------------------------------------------------------------- #
# scoring is unchanged (existing behaviour, re-pinned here)
# --------------------------------------------------------------------------- #

def _seed_for(fault_id: str, limit: int = 500) -> int:
    for seed in range(limit):
        if fault_mod.realized_fault(_CARD, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


def test_the_seeded_fault_scores_correct(tmp_path):
    seed = _seed_for("low_voltage")
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    outcome = answer(run_id, "low_voltage", state_dir=state_dir)
    assert outcome["correct"] is True
    assert outcome["disqualified"] is False


def test_a_wrong_answer_scores_wrong(tmp_path):
    seed = _seed_for("low_voltage")
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    outcome = answer(run_id, "overheat", state_dir=state_dir)
    assert outcome["correct"] is False


def test_an_answer_with_no_measurement_is_disqualified(tmp_path):
    seed = _seed_for("low_voltage")
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    outcome = answer(run_id, "low_voltage", state_dir=state_dir)
    assert outcome["disqualified"] is True


# --------------------------------------------------------------------------- #
# healthy seeds already exist: at least 20% of seeds 0-29 are `ok`, `ok` is
# the correct answer there, and naming a fault there is a false_fail -- this
# pins existing behaviour and adds no new fault-draw logic (#461)
# --------------------------------------------------------------------------- #

def test_at_least_20_percent_of_seeds_0_to_29_are_healthy():
    healthy = [s for s in range(30) if fault_mod.realized_fault(_CARD, s).fault_id == "ok"]
    assert len(healthy) >= 6, (  # 20% of 30
        f"only {len(healthy)}/30 seeds are healthy: {healthy}")


@pytest.mark.parametrize("seed", [s for s in range(30)
                                  if fault_mod.realized_fault(_CARD, s).fault_id == "ok"])
def test_ok_scores_correct_on_a_healthy_seed(seed, tmp_path):
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    outcome = answer(run_id, "ok", state_dir=state_dir)
    assert outcome["correct"] is True
    assert outcome["score"]["false_fails"] == 0


@pytest.mark.parametrize("seed", [s for s in range(30)
                                  if fault_mod.realized_fault(_CARD, s).fault_id == "ok"])
def test_naming_a_fault_on_a_healthy_seed_is_a_false_fail(seed, tmp_path):
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    outcome = answer(run_id, "low_voltage", state_dir=state_dir)
    assert outcome["correct"] is False
    assert outcome["score"]["false_fails"] == 1
