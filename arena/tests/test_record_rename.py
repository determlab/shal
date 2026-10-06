"""issue #435 CTO review: a capture made before the `record.json` ->
`arena-record.json` rename (never migrated, Scope: "no migration") must
still be readable -- `export` and `verify` (the replay result card and the
Watch page) both read through `RunStore.record_path`, which falls back to
the old bare name when the new one is absent."""
from __future__ import annotations

from pathlib import Path

from shal_arena.replay.card import load_card_data
from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _close_a_run_then_revert_to_the_old_filename(tmp_path: Path) -> str:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    store = RunStore(tmp_path)
    new_path = tmp_path / f"{run_id}.arena-record.json"
    old_path = tmp_path / f"{run_id}.record.json"
    assert new_path.is_file()
    new_path.rename(old_path)
    assert store.record_path(run_id) == old_path
    return run_id


def test_record_path_falls_back_to_the_old_bare_name(tmp_path: Path) -> None:
    run_id = _close_a_run_then_revert_to_the_old_filename(tmp_path)
    store = RunStore(tmp_path)
    assert store.record_path(run_id) == tmp_path / f"{run_id}.record.json"


def test_ui_payload_reads_an_old_layout_capture(tmp_path: Path) -> None:
    run_id = _close_a_run_then_revert_to_the_old_filename(tmp_path)
    payload = run_payload(run_id, state_dir=tmp_path)
    assert payload["record"]["given"] == "ok"
    assert payload["answer_sentence"] is not None


def test_export_reads_an_old_layout_capture(tmp_path: Path) -> None:
    run_id = _close_a_run_then_revert_to_the_old_filename(tmp_path)
    html = build_export(run_id, state_dir=tmp_path)
    assert "Replay of a recorded run" in html


def test_replay_card_reads_an_old_layout_capture(tmp_path: Path) -> None:
    run_id = _close_a_run_then_revert_to_the_old_filename(tmp_path)
    data = load_card_data(run_id, state_dir=tmp_path)
    assert data.record["given"] == "ok"


def test_a_fresh_run_still_writes_only_the_new_name(tmp_path: Path) -> None:
    """The rename only applies to READING an old capture -- a run closed
    on this branch never writes the old bare name at all."""
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    assert (tmp_path / f"{run_id}.arena-record.json").is_file()
    assert not (tmp_path / f"{run_id}.record.json").is_file()
