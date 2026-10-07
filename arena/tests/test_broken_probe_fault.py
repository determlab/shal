"""issue #478: relay-rail's `broken_probe` fault. The DMM reads about 0 V,
but the card is good: the supply current and the regulator temperature read
as on an `ok` seed. The right answer is `probe`; it counts in
`error_fail_correct`, never in `faults_caught`, and blaming the card there is
a false fail.

Adding a fifth fault re-maps relay-rail's seeds, so each run now stores the
fault list it was drawn from (game 0.4.2). A run stored before that (no
list) is read as the old 4-fault list, and still replays and renders.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.replay.card import build_result_card
from shal_arena.runner import (
    answer,
    drive_input,
    pick_fault,
    run_fault_ids,
    start_run,
    take_measurement,
)
from shal_arena.score import GAME_VERSION, build_score
from shal_arena.store import RunStore

from . import test_game_version_golden as golden
from .conftest import FIXTURES, PASSING_DMM_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK

#: relay-rail's seed -> fault map for seeds 0-29 under game 0.4.2
SEED_MAP_0_4_2 = {
    0: "overheat", 1: "low_voltage", 2: "ok", 3: "low_voltage", 4: "low_voltage",
    5: "broken_probe", 6: "broken_probe", 7: "open", 8: "low_voltage", 9: "overheat",
    10: "broken_probe", 11: "overheat", 12: "overheat", 13: "open", 14: "ok",
    15: "low_voltage", 16: "open", 17: "broken_probe", 18: "low_voltage", 19: "ok",
    20: "low_voltage", 21: "low_voltage", 22: "low_voltage", 23: "open", 24: "overheat",
    25: "overheat", 26: "low_voltage", 27: "overheat", 28: "ok", 29: "broken_probe",
}
#: the same seeds under the old 4-fault list (game 0.4.1): only these differ
_OLD_FAULT_AT = {5: "open", 6: "ok", 10: "ok", 17: "overheat", 29: "ok"}

BROKEN_PROBE_SEEDS = [s for s, f in SEED_MAP_0_4_2.items() if f == "broken_probe"]
OK_SEED = 2
OPEN_SEED = 7
CARD_FAULT_ANSWERS = ("low_voltage", "open", "overheat")

#: the fixtures' `task_path` is repo-relative
_REPO_ROOT = Path(__file__).resolve().parents[2]
_OLD_RELAY_RAIL_RUN = FIXTURES / "runs" / "main-0.4.1-relay-rail-open"
_OLD_EASY_RUN = FIXTURES / "runs" / "main-0.4.0-open"
_RULE_KEYS = ("task_id", "seed", "fault_type", "faults_total", "faults_caught",
              "false_fails", "error_fail_correct", "turns", "gate_stops", "schema_version")


def _card():
    return load_task(str(RELAY_RAIL_TASK)).card


def _play(seed: int, state_dir: Path) -> tuple[str, dict, dict]:
    """Start a relay-rail run, power it at 12.0 V, measure dmm0 and temp0."""
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=state_dir)
    dmm = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    temp = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)
    return run_id, dmm, temp


def test_seed_map_for_seeds_0_to_29():
    card = _card()
    assert {s: fault_mod.realized_fault(card, s).fault_id for s in range(30)} == SEED_MAP_0_4_2
    old = fault_mod.legacy_fault_ids(card)
    assert old == ["ok", "low_voltage", "open", "overheat"]
    changed = {s: fault_mod.realized_fault(card, s, old).fault_id for s in range(30)
               if fault_mod.realized_fault(card, s, old).fault_id != SEED_MAP_0_4_2[s]}
    assert changed == _OLD_FAULT_AT


@pytest.mark.parametrize("seed", BROKEN_PROBE_SEEDS)
def test_broken_probe_reads_near_zero_with_ok_current_and_temperature(seed, tmp_path):
    """DoD 1: dmm0 reads below 5% of nominal; supply current and temp0 read
    the same as on an `ok` seed; no HopError (the measure would raise)."""
    rail = next(r for r in _card().rails if r.name == "3v3")
    _, dmm, temp = _play(seed, tmp_path / "bp")
    _, ok_dmm, ok_temp = _play(OK_SEED, tmp_path / "ok")
    assert abs(dmm["reading"]) < 0.05 * rail.nominal_v
    assert ok_dmm["reading"] == pytest.approx(rail.nominal_v)
    assert dmm["card"] == ok_dmm["card"]
    assert dmm["card"]["supply_a"] > 0
    assert dmm["current_a"] == ok_dmm["current_a"]
    assert temp["reading"] == ok_temp["reading"]
    assert temp["card"] == ok_temp["card"]


def test_broken_probe_reads_the_same_value_every_time(tmp_path):
    """The same seed gives the same reading on every call and every OS: the
    value is pinned, not just compared run against run."""
    first = _play(5, tmp_path / "a")[1]["reading"]
    again = _play(5, tmp_path / "b")[1]["reading"]
    assert first == again == 0.012286
    rail = next(r for r in _card().rails if r.name == "3v3")
    assert fault_mod.broken_probe_residual_v(rail, 5) == first


@pytest.mark.parametrize("seed", BROKEN_PROBE_SEEDS)
def test_answer_probe_is_correct_and_counts_in_error_fail_correct(seed, tmp_path):
    """DoD 2 and 4."""
    run_id, _, _ = _play(seed, tmp_path)
    out = answer(run_id, "probe", state_dir=tmp_path)
    assert out["correct"] is True and out["disqualified"] is False
    assert out["fault_id"] == "broken_probe"
    score = out["score"]
    assert score["error_fail_correct"] == 1
    assert score["false_fails"] == 0
    assert score["faults_total"] == 0 and score["faults_caught"] == 0


@pytest.mark.parametrize("given", CARD_FAULT_ANSWERS)
def test_blaming_the_card_on_a_broken_probe_is_a_false_fail(given, tmp_path):
    """DoD 3 and 4: any card-fault answer is wrong and a false fail."""
    run_id, _, _ = _play(BROKEN_PROBE_SEEDS[0], tmp_path)
    out = answer(run_id, given, state_dir=tmp_path)
    assert out["correct"] is False
    score = out["score"]
    assert score["false_fails"] == 1
    assert score["error_fail_correct"] == 0
    assert score["faults_total"] == 0 and score["faults_caught"] == 0


def test_open_seed_draws_about_zero_amps_unlike_broken_probe(tmp_path):
    """DoD 7: on an `open` seed (#477) the supply current is about 0 A, and
    it differs from a `broken_probe` seed's."""
    _, open_dmm, _ = _play(OPEN_SEED, tmp_path / "open")
    _, bp_dmm, _ = _play(BROKEN_PROBE_SEEDS[0], tmp_path / "bp")
    assert open_dmm["card"]["supply_a"] == pytest.approx(0.0, abs=1e-3)
    assert open_dmm["current_a"] == pytest.approx(0.0, abs=1e-3)
    assert bp_dmm["card"]["supply_a"] != open_dmm["card"]["supply_a"]
    assert bp_dmm["current_a"] != open_dmm["current_a"]
    # both read about 0 V on the DMM: only the other instruments tell them apart
    assert abs(open_dmm["reading"]) < 0.165 and abs(bp_dmm["reading"]) < 0.165


def test_ok_seed_scores_as_before(tmp_path):
    run_id, _, _ = _play(OK_SEED, tmp_path)
    score = answer(run_id, "ok", state_dir=tmp_path)["score"]
    assert (score["faults_total"], score["faults_caught"], score["false_fails"],
            score["error_fail_correct"]) == (1, 0, 0, 0)


def test_game_version_is_0_4_2_and_the_0_4_1_hash_is_kept():
    """DoD 5 (the golden hash itself is checked by test_game_version_golden)."""
    assert GAME_VERSION == "0.4.2"
    assert "0.4.2" in golden.GOLDEN
    assert golden.GOLDEN["0.4.1"] == (
        "85ae276276b0a8cae924b04b4918756b2f0fb578609e7330b15098495aff1be0")


def test_a_new_run_stores_its_fault_list_and_game_version(tmp_path):
    """DoD 6, second half."""
    run_id = start_run(str(RELAY_RAIL_TASK), seed=5, state_dir=tmp_path)["run_id"]
    doc = json.loads((tmp_path / f"{run_id}.json").read_text(encoding="utf-8"))
    # positions in the card's `faults:` list -- the file names no fault
    assert doc["fault_indices"] == [0, 1, 2, 3, 4]
    assert doc["game_version"] == "0.4.2"
    state = RunStore(tmp_path).load(run_id)
    assert run_fault_ids(_card(), state) == [
        "ok", "low_voltage", "open", "overheat", "broken_probe"]


def _replay(fixture: Path, tmp_path: Path) -> tuple[str, Path, dict, dict]:
    state_dir = tmp_path / "state"
    shutil.copytree(fixture, state_dir)
    run_id = next(state_dir.glob("*.score.json")).name.removesuffix(".score.json")
    record = json.loads((state_dir / f"{run_id}.arena-record.json").read_text(encoding="utf-8"))
    score = json.loads((state_dir / f"{run_id}.score.json").read_text(encoding="utf-8"))
    return run_id, state_dir, record, score


def test_an_old_relay_rail_run_replays_to_the_same_fault_and_score(tmp_path):
    """DoD 6, first half: a relay-rail run made on main under game 0.4.1
    (seed 5, drawn from the 4-fault list, where seed 5 is `open`; under
    0.4.2 a new seed-5 run is `broken_probe`) stored no fault list. It still
    replays to `open` and to the same score, and renders. `shal-arena
    verify` (#393) does not exist yet, so there is nothing to verify with."""
    run_id, state_dir, record, score = _replay(_OLD_RELAY_RAIL_RUN, tmp_path)
    assert score["game_version"] == "0.4.1" and score["seed"] == 5
    state = RunStore(state_dir).load(run_id)
    assert state.fault_indices is None and state.game_version is None

    card = load_task(str(_REPO_ROOT / state.task_path)).card
    fault_ids = run_fault_ids(card, state)
    assert fault_ids == ["ok", "low_voltage", "open", "overheat"]
    assert pick_fault(card, state.seed, fault_ids) == record["fault_id"] == "open"
    assert pick_fault(card, state.seed, [f.id for f in card.faults]) == "broken_probe"

    again = build_score(task_id=score["task_id"], seed=state.seed, fault_id=record["fault_id"],
                        given=record["given"], correct=record["correct"],
                        disqualified=False, created_at=state.created_at,
                        closed_at=record["closed_at"], turns=state.turns,
                        record_path=RunStore(state_dir).record_path(run_id))
    assert {k: again[k] for k in _RULE_KEYS} == {k: score[k] for k in _RULE_KEYS}
    # the stored score still hashes this record (git may check the fixture
    # out with CRLF on Windows; the run wrote it with LF)
    record_bytes = RunStore(state_dir).record_path(run_id).read_bytes()
    assert hashlib.sha256(record_bytes.replace(b"\r\n", b"\n")).hexdigest() == (
        score["record_sha256"])

    html = build_result_card(run_id, state_dir=state_dir)
    assert "1 of 1 faults caught." in html
    assert "seed 5" in html and "game 0.4.1" in html


def test_an_old_0_4_0_run_still_replays_to_its_fault(tmp_path):
    """The 0.4.0 `open` fixture from #480 stored no fault list either."""
    run_id, state_dir, record, _ = _replay(_OLD_EASY_RUN, tmp_path)
    state = RunStore(state_dir).load(run_id)
    card = load_task(str(_REPO_ROOT / state.task_path)).card
    assert pick_fault(card, state.seed, run_fault_ids(card, state)) == record["fault_id"]
    assert "1 of 1 faults caught." in build_result_card(run_id, state_dir=state_dir)
