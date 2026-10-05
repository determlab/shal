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
from shal_arena.runner import (
    NotSupported,
    answer,
    check_instrument_driver,
    drive_input,
    pick_fault,
    start_run,
)
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


def test_drive_input_overvoltage_damages_card_and_logs_it(tmp_path: Path) -> None:
    """issue #313 Agent path: `CardSim.state`/`apply_input`, reachable through
    the runner. buck-5v-3v3.yaml's `vin` is destroyed above 6.0 V."""
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]

    drive = drive_input(run_id, "psu0", 6.5, state_dir=tmp_path)
    assert drive["ok"] is True
    assert drive["side_effect"] == "write"
    assert drive["state"] == "damage"

    sim_log_path = RunStore(tmp_path).sim_log_path(run_id)
    lines = [json.loads(ln) for ln in sim_log_path.read_text(encoding="utf-8").splitlines()]
    assert any(ln["kind"] == "damage" and ln["address"] == "psu0" for ln in lines)

    # the card stays destroyed for the rest of the run — restored from the
    # run's own persisted state on this SECOND, separate `drive_input` call.
    again = drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    assert again["state"] == "damage"


def test_drive_input_in_range_leaves_card_ok(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    drive = drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    assert drive["state"] == "ok"
    assert drive["side_effect"] == "write"


def test_drive_input_unknown_address_names_the_fix(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    with pytest.raises(CheckCouldNotRun) as ei:
        drive_input(run_id, "no-such-address", 5.0, state_dir=tmp_path)
    assert ei.value.fix


def test_drive_input_on_a_probe_instrument_is_refused(tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]
    with pytest.raises(CheckCouldNotRun) as ei:
        drive_input(run_id, "dmm0", 5.0, state_dir=tmp_path)  # dmm0 probes, it doesn't drive
    assert ei.value.fix


def test_number_kind_answers_are_not_supported_yet(tmp_path: Path, minimal_task: Path) -> None:
    import yaml
    doc = yaml.safe_load(minimal_task.read_text(encoding="utf-8"))
    doc["question"]["answer"] = {"kind": "number", "unit": "V", "tol": 0.02}
    minimal_task.write_text(yaml.safe_dump(doc), encoding="utf-8")
    result = start_run(minimal_task, state_dir=tmp_path)
    with pytest.raises(NotSupported):
        answer(result["run_id"], "3.3", state_dir=tmp_path)
