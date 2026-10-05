"""issue #312 Done-when (beyond test_fault_seed.py):

- the files in a generated run folder the player can read do not contain the
  fault name, or a fault-specific word (CTO review on #322: a word that only
  one fault's run could ever produce names that fault just as surely as its
  id would);
- an answer with no measurement in the sim log is disqualified;
- the score file validates against the 13-field schema, and ``record_sha256``
  matches the record.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shal_arena.errors import MeasurementFailed
from shal_arena.fault import realized_fault
from shal_arena.loader import load_task
from shal_arena.runner import (
    answer,
    check_instrument_driver,
    pick_fault,
    start_run,
    take_measurement,
)
from shal_arena.score import validate_score
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_DRIVER, SAMPLE_TASK

# CTO review on #322, point 4: scan for these alongside the fault id itself —
# each is specific enough to one fault that writing it to a player-readable
# file would name that fault just as surely as its id would.
_LEAK_WORDS = ("unreachable", "shift_v", "ripple_vpp", "noise", "attempt")


def _expected_fault(run_id: str, state_dir: Path) -> str:
    state = RunStore(state_dir).load(run_id)
    return pick_fault(load_task(state.task_path).card, state.seed)


def _seed_for(fault_id: str, limit: int = 300) -> int:
    card = load_task(SAMPLE_TASK).card
    for seed in range(limit):
        if realized_fault(card, seed).fault_id == fault_id:
            return seed
    raise AssertionError(f"no seed under {limit} realizes fault {fault_id!r}")


@pytest.mark.parametrize("fault_id", ["ok", "low_voltage", "noise", "open"])
def test_generated_run_folder_never_names_the_fault(fault_id: str, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for(fault_id)
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    assert _expected_fault(run_id, state_dir) == fault_id

    check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    try:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:
        pass  # 'open': the attempt itself is a live error, never written anywhere

    found = []
    for path in state_dir.rglob("*"):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if path.name == f"{run_id}.json":
            # the run's own "open"/"closed" lifecycle status is present for
            # EVERY run regardless of which fault is active — it carries no
            # information about the fault (unlike a word only one fault's
            # run could produce), so it is excluded rather than skipped
            # wholesale: everything else in this file still gets scanned.
            doc = json.loads(text)
            doc.pop("status", None)
            text = json.dumps(doc)
        hits = [w for w in (fault_id, *_LEAK_WORDS) if w in text]
        if hits:
            found.append((path, hits))
    assert not found, f"leaked fault-specific content while the run is open: {found}"


def test_answer_with_no_measurement_is_disqualified(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    # no measurement at all for dmm0 (the probe instrument) — never measured
    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is True
    assert out["score"]["faults_caught"] == 0


def test_checking_the_driver_alone_does_not_count_as_measuring(tmp_path: Path) -> None:
    """CTO review on #322, point 3: `check-driver` validates the driver — it
    must not also silently satisfy the "did you measure" requirement, since
    every player runs it regardless of whether they intend to measure yet."""
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    check = check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert check["passed"] is True
    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is True


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
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    fault_id = _expected_fault(run_id, state_dir)

    out = answer(run_id, fault_id, state_dir=state_dir)

    assert out["disqualified"] is False


def test_score_file_validates_and_ties_to_the_record(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
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
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "low_voltage", state_dir=state_dir)

    assert out["correct"] is False
    assert out["score"]["false_fails"] == 1
    assert out["score"]["faults_caught"] == 0


def test_correct_answer_on_a_measured_real_fault_is_caught(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("low_voltage")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    measurement = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert measurement["reading"] != pytest.approx(3.3)  # the shift is real, not bookkeeping

    out = answer(run_id, "low_voltage", state_dir=state_dir)

    assert out["correct"] is True
    assert out["disqualified"] is False
    assert out["score"]["faults_caught"] == 1
    assert out["score"]["false_fails"] == 0


def test_correct_ok_answer_is_not_counted_as_a_caught_fault(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    seed = _seed_for("ok")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "ok", state_dir=state_dir)

    assert out["correct"] is True
    assert out["score"]["faults_caught"] == 0
    assert out["score"]["false_fails"] == 0


def test_open_fault_fails_the_measurement_and_leaves_the_run_disqualified(
        tmp_path: Path) -> None:
    """The `open` fault makes the instrument genuinely unreachable
    (`fault.harness_for_run` extends `fault: unplugged`). `take_measurement`
    reports that live, to the player, as `MeasurementFailed` — and writes
    nothing to the sim log (CTO review on #322: any entry distinguishing
    "this one raised" from "this one didn't" would, in practice, name the
    `open` fault, since a correct driver only ever fails to read because of
    it). The player can still reason from the live error to answer `open`
    correctly; the run is `disqualified` either way, for want of a logged
    reading — same rule applied uniformly, not relaxed for this one fault."""
    state_dir = tmp_path / "state"
    seed = _seed_for("open")
    result = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)
    run_id = result["run_id"]

    check = check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    assert check["passed"] is True  # the ADK check is about driver correctness, not the card

    with pytest.raises(MeasurementFailed):
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)

    out = answer(run_id, "open", state_dir=state_dir)

    assert out["correct"] is True
    assert out["disqualified"] is True
    assert out["score"]["faults_caught"] == 0
    assert out["score"]["error_fail_correct"] == 0
