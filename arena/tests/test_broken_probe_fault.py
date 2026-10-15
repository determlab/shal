"""issue #478: relay-rail's `broken_probe` fault. The DMM reads about 0 V, but
the card is good: the supply current and the regulator temperature are those
of an `ok` card, and the right answer is `probe`, not a card fault."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.runner import answer, pick_fault, start_run, take_measurement
from shal_arena.score import GAME_VERSION
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK

_CARD = load_task(RELAY_RAIL_TASK).card
_NOMINAL_V = 3.3
_SEEDS = range(30)
_PROBE_SEEDS = [s for s in _SEEDS if fault_mod.realized_fault(_CARD, s).fault_id == "broken_probe"]
_OK_SEED = next(s for s in _SEEDS if fault_mod.realized_fault(_CARD, s).fault_id == "ok")
_OPEN_SEED = next(s for s in _SEEDS if fault_mod.realized_fault(_CARD, s).fault_id == "open")
_LEGACY_IDS = ["ok", "low_voltage", "open", "overheat"]


def _start(tmp_path: Path, seed: int) -> tuple[str, Path]:
    state_dir = tmp_path / "state"
    return start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"], state_dir


def _measure_all(tmp_path: Path, seed: int) -> dict:
    run_id, state_dir = _start(tmp_path, seed)
    dmm = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    temp = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)
    return {"dmm_v": dmm["reading"], "supply_a": dmm["card"]["supply_a"],
            "current_a": dmm.get("current_a"), "temp_c": temp["reading"]}


def test_there_are_broken_probe_seeds_in_0_to_29():
    assert _PROBE_SEEDS


@pytest.mark.parametrize("seed", _PROBE_SEEDS)
def test_the_dmm_reads_near_zero_while_current_and_temperature_are_ok(seed, tmp_path):
    got = _measure_all(tmp_path / "probe", seed)
    ok = _measure_all(tmp_path / "ok", _OK_SEED)
    assert got["dmm_v"] < 0.05 * _NOMINAL_V
    assert got["supply_a"] == ok["supply_a"] > 0
    assert got["current_a"] == ok["current_a"]
    assert got["temp_c"] == ok["temp_c"]


def test_the_same_seed_reads_the_same_value_twice(tmp_path):
    seed = _PROBE_SEEDS[0]
    assert (_measure_all(tmp_path / "a", seed)["dmm_v"]
            == _measure_all(tmp_path / "b", seed)["dmm_v"])


def test_an_open_card_draws_no_supply_current_a_broken_probe_does(tmp_path):
    opened = _measure_all(tmp_path / "open", _OPEN_SEED)
    probe = _measure_all(tmp_path / "probe", _PROBE_SEEDS[0])
    assert opened["supply_a"] == 0.0
    assert probe["supply_a"] > 0.0
    assert opened["supply_a"] != probe["supply_a"]
    assert opened["dmm_v"] < 0.05 * _NOMINAL_V


@pytest.mark.parametrize("seed", _PROBE_SEEDS)
def test_probe_is_the_correct_answer(seed, tmp_path):
    run_id, state_dir = _start(tmp_path, seed)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    out = answer(run_id, "probe", state_dir=state_dir)
    assert out["correct"] is True
    assert out["score"]["fault_type"] == "broken_probe"
    assert out["score"]["error_fail_correct"] == 1
    assert out["score"]["false_fails"] == 0
    assert out["score"]["faults_total"] == 0 and out["score"]["faults_caught"] == 0


@pytest.mark.parametrize("given", ["open", "low_voltage", "overheat"])
def test_a_card_fault_answer_is_a_false_fail(given, tmp_path):
    run_id, state_dir = _start(tmp_path, _PROBE_SEEDS[0])
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    out = answer(run_id, given, state_dir=state_dir)
    assert out["correct"] is False
    assert out["score"]["false_fails"] == 1
    assert out["score"]["error_fail_correct"] == 0
    assert out["score"]["faults_total"] == 0 and out["score"]["faults_caught"] == 0


def test_probe_is_not_error_fail_correct_when_disqualified(tmp_path):
    run_id, state_dir = _start(tmp_path, _PROBE_SEEDS[0])
    out = answer(run_id, "probe", state_dir=state_dir)  # never measured
    assert out["disqualified"] is True
    assert out["score"]["error_fail_correct"] == 0


def test_ok_behaves_as_before(tmp_path):
    run_id, state_dir = _start(tmp_path, _OK_SEED)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    out = answer(run_id, "ok", state_dir=state_dir)
    assert out["correct"] is True
    assert out["score"]["faults_total"] == 1 and out["score"]["false_fails"] == 0
    assert out["score"]["error_fail_correct"] == 0


def test_a_new_run_stores_the_new_game_version(tmp_path):
    run_id, state_dir = _start(tmp_path, _OK_SEED)
    assert RunStore(state_dir).load(run_id).game_version == GAME_VERSION == "0.4.2"
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert answer(run_id, "ok", state_dir=state_dir)["score"]["game_version"] == GAME_VERSION


def _age_run(state_dir: Path, run_id: str, *, version: str | None) -> None:
    """Make the run look like one stored before this change (game 0.4.1, or no
    version at all: the state file then has no `game_version` key)."""
    path = RunStore(state_dir)._public_path(run_id)
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc.pop("game_version", None)
    if version is not None:
        doc["game_version"] = version
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


@pytest.mark.parametrize("version", ["0.4.1", None])
@pytest.mark.parametrize("seed", _SEEDS)
def test_an_old_run_still_replays_to_its_old_fault_and_score(seed, version, tmp_path):
    old_fault = fault_mod.realized_fault(_CARD, seed, _LEGACY_IDS).fault_id
    assert old_fault != "broken_probe"
    run_id, state_dir = _start(tmp_path, seed)
    _age_run(state_dir, run_id, version=version)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    out = answer(run_id, old_fault, state_dir=state_dir)
    assert out["correct"] is True
    assert out["score"]["fault_type"] == old_fault
    assert out["score"]["game_version"] == "0.4.1"
    assert out["score"]["faults_total"] == 1


def test_the_old_list_gives_a_different_map_than_the_new_one():
    """The remap this change makes for relay-rail only, which is why the old
    list has to be kept for old runs."""
    new = [pick_fault(_CARD, s) for s in _SEEDS]
    old = [pick_fault(_CARD, s, _LEGACY_IDS) for s in _SEEDS]
    assert new != old
    assert "broken_probe" in new and "broken_probe" not in old
