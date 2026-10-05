"""issue #312 Done-when (beyond test_fault_seed.py):

- the files in a generated run folder the player can read do not contain the
  fault name;
- an answer with no measurement in the sim log is disqualified;
- the score file validates against the 13-field schema, and ``record_sha256``
  matches the record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from shal_arena.fault import realized_fault
from shal_arena.loader import load_task
from shal_arena.runner import answer, check_instrument_driver, pick_fault, start_run
from shal_arena.score import validate_score
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_DRIVER, SAMPLE_TASK


def _expected_fault(run_id: str, state_dir: Path) -> str:
    state = RunStore(state_dir).load(run_id)
    return pick_fault(load_task(state.task_path).card, state.seed)


def _seed_for(fault_id: str, limit: int = 300) -> int:
    card = load_task(SAMPLE_TASK).card
    for seed in range(limit):
        if realized_fault(card, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


def test_generated_run_folder_never_names_the_fault(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    found = []
    for path in state_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if fault_id in text:
                found.append(path)
    assert not found, f"{found} contain the hidden fault {fault_id!r}"


def test_answer_with_no_measurement_is_disqualified(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    # no `check` call at all for dmm0 (the probe instrument) — never measured
    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is True
    assert out["score"]["faults_caught"] == 0


def test_checking_only_the_drives_instrument_still_disqualifies(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    # psu0 `drives:` the card; it is not what the question asks about
    check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=state_dir)
    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is True


def test_answer_after_a_measurement_is_not_disqualified(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    fault_id = _expected_fault(run_id, state_dir)

    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is False


def test_score_file_validates_and_ties_to_the_record(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    fault_id = _expected_fault(run_id, state_dir)

    out = answer(run_id, fault_id, state_dir=state_dir)
    validate_score(out["score"])  # raises if the 13-field schema is violated
    assert set(out["score"]) == {
        "task_id", "seed", "fault_type", "faults_total", "faults_caught",
        "false_fails", "error_fail_correct", "duration_s", "turns",
        "gate_stops", "schema_version", "game_version", "record_sha256",
    }

    store = RunStore(state_dir)
    on_disk = json.loads(store.score_path(run_id).read_text(encoding="utf-8"))
    assert on_disk == out["score"]

    record_bytes = store.record_path(run_id).read_bytes()
    assert out["score"]["record_sha256"] == hashlib.sha256(record_bytes).hexdigest()


def test_false_fail_counted_when_a_healthy_card_is_blamed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("ok")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "low_voltage", state_dir=state_dir)

    assert out["correct"] is False
    assert out["score"]["false_fails"] == 1
    assert out["score"]["faults_caught"] == 0


def test_correct_answer_on_a_measured_real_fault_is_caught(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("low_voltage")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "low_voltage", state_dir=state_dir)

    assert out["correct"] is True
    assert out["score"]["faults_caught"] == 1
    assert out["score"]["false_fails"] == 0


def test_correct_ok_answer_is_not_counted_as_a_caught_fault(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("ok")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "ok", state_dir=state_dir)

    assert out["correct"] is True
    assert out["score"]["faults_caught"] == 0
    assert out["score"]["false_fails"] == 0


def test_open_fault_is_detected_as_unreachable_and_counted(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("open")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    check = check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert check["passed"] is True  # the ADK check is about driver correctness, not the card

    out = answer(run_id, "open", state_dir=state_dir)

    assert out["disqualified"] is False  # the attempt was logged even though unreachable
    assert out["correct"] is True
    assert out["score"]["error_fail_correct"] == 1
