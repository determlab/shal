"""The runner (issue #310 Scope): start a run, light a tile with the
ADK-style driver check, answer and close it. DoD: `pytest
arena/tests/test_runner.py -q` passes; no file the player can read contains
the hidden fault."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shal_arena.errors import CheckCouldNotRun, UnknownRun
from shal_arena.loader import load_task
from shal_arena.runner import NotSupported, answer, check_instrument_driver, pick_fault, start_run
from shal_arena.store import RunStore

from .conftest import FAILING_DRIVER, PASSING_DRIVER, SAMPLE_TASK


def _expected_fault(run_id: str, state_dir: Path) -> str:
    """Test-only: recompute the fault the same way `answer` does, from the
    run's own public state — never a peek at anything secret, because
    nothing secret is persisted any more (CTO review on #319)."""
    state = RunStore(state_dir).load(run_id)
    loaded = load_task(state.task_path)
    return pick_fault(loaded.card, state.seed)


def test_start_run_returns_task_text_instrument_list_and_run_id(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    assert result["ok"] is True
    assert result["side_effect"] == "write"
    assert result["run_id"]
    assert result["task"]["question"]
    addresses = {i["address"] for i in result["instruments"]}
    assert addresses == {"psu0", "dmm0"}
    for inst in result["instruments"]:
        assert inst["datasheet"]                      # the player gets the datasheet
        assert "drives" in inst or "probe" in inst


def test_no_file_the_player_can_read_contains_the_hidden_fault(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, state_dir)

    assert "fault_id" not in json.dumps(result)

    # Every file under state_dir while the run is open — not just the public
    # run JSON this test used to check alone (CTO review on #319: that narrower
    # check passed even while a *.secret.json sat right next to it on disk).
    for path in state_dir.rglob("*"):
        if path.is_file():
            assert fault_id not in path.read_text(encoding="utf-8"), (
                f"{path} contains the hidden fault {fault_id!r}")


def test_check_instrument_driver_lights_tile_on_pass(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    check = check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=tmp_path)
    assert check["passed"] is True
    assert check["problems"] == []
    state = RunStore(tmp_path).load(run_id)
    assert state.tiles["psu0"].passed is True


def test_check_instrument_driver_does_not_light_tile_on_problems(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    check = check_instrument_driver(run_id, "psu0", FAILING_DRIVER, state_dir=tmp_path)
    assert check["passed"] is False
    assert check["problems"]
    state = RunStore(tmp_path).load(run_id)
    assert state.tiles["psu0"].passed is False


def test_check_unknown_address_names_the_valid_ones(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    with pytest.raises(CheckCouldNotRun) as ei:
        check_instrument_driver(run_id, "no-such-address", PASSING_DRIVER, state_dir=tmp_path)
    assert "psu0" in ei.value.fix and "dmm0" in ei.value.fix


def test_check_missing_driver_file_names_the_fix(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    with pytest.raises(CheckCouldNotRun) as ei:
        check_instrument_driver(run_id, "psu0", tmp_path / "missing.py", state_dir=tmp_path)
    assert ei.value.fix


def test_answer_correct_closes_the_run_and_reveals_the_fault(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, tmp_path)
    record = answer(run_id, fault_id, state_dir=tmp_path)
    assert record["correct"] is True
    assert record["fault_id"] == fault_id
    state = RunStore(tmp_path).load(run_id)
    assert state.status == "closed"


def test_answer_incorrect_is_recorded_as_such(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    fault_id = _expected_fault(run_id, tmp_path)
    wrong = next(v for v in ("ok", "low_voltage", "noise", "open") if v != fault_id)
    record = answer(run_id, wrong, state_dir=tmp_path)
    assert record["correct"] is False


def test_cannot_answer_a_run_twice(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    answer(run_id, "ok", state_dir=tmp_path)
    with pytest.raises(UnknownRun):
        answer(run_id, "ok", state_dir=tmp_path)


def test_answer_on_unknown_run_id_names_the_fix(tmp_path: Path) -> None:
    with pytest.raises(UnknownRun) as ei:
        answer("run-does-not-exist", "ok", state_dir=tmp_path)
    assert ei.value.fix


def test_number_kind_answers_are_not_supported_yet(tmp_path: Path, minimal_task: Path) -> None:
    import yaml
    doc = yaml.safe_load(minimal_task.read_text(encoding="utf-8"))
    doc["question"]["answer"] = {"kind": "number", "unit": "V", "tol": 0.02}
    minimal_task.write_text(yaml.safe_dump(doc), encoding="utf-8")
    result = start_run(minimal_task, state_dir=tmp_path)
    with pytest.raises(NotSupported):
        answer(result["run_id"], "3.3", state_dir=tmp_path)
