"""issue #521: the result card, the Play page's data and score.json agree.

A correct `ok` on a good card is never "0 of 1 faults caught", and a refused
drive is the same gate stop in score.json as on the Play page (which counts
the `refused` rows of the run's own timeline).
"""
from __future__ import annotations

import json
from pathlib import Path

from shal_arena.replay.card import build_result_card
from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _score(state_dir: Path, run_id: str) -> dict:
    return json.loads(RunStore(state_dir).score_path(run_id).read_text(encoding="utf-8"))


def _closed_run(tmp_path: Path, *, refuse: bool, want_ok: bool) -> tuple[str, Path]:
    for seed in range(60):
        state_dir = tmp_path / f"s{seed}"
        run_id = start_run(SAMPLE_TASK, seed=seed, state_dir=state_dir)["run_id"]
        if refuse:
            refusal = drive_input(run_id, "psu0", 99.0, state_dir=state_dir)
            assert refusal["sent"] is False
        try:
            take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
        except Exception:  # an `open` card fails the read live; still a measure turn
            pass
        answer(run_id, "ok", state_dir=state_dir)
        if (_score(state_dir, run_id)["fault_type"] == "ok") == want_ok:
            return run_id, state_dir
    raise AssertionError("no seed with the wanted fault in 0..59")


def test_correct_ok_on_a_good_card_is_not_a_missed_fault(tmp_path: Path) -> None:
    run_id, state_dir = _closed_run(tmp_path, refuse=False, want_ok=True)
    score = _score(state_dir, run_id)
    assert score["fault_type"] == "ok"
    assert score["false_fails"] == 0 and score["faults_caught"] == 0
    card =build_result_card(run_id, state_dir=state_dir)
    assert "0 of 1" not in card
    assert "faults caught" not in card
    assert "0 false fails" in card


def test_a_real_fault_still_counts_as_one_total(tmp_path: Path) -> None:
    run_id, state_dir = _closed_run(tmp_path, refuse=False, want_ok=False)
    score = _score(state_dir, run_id)
    assert score["faults_total"] == 1
    assert f"{score['faults_caught']} of 1 faults caught" in build_result_card(
        run_id, state_dir=state_dir)


def test_play_and_score_json_agree_on_gate_stops(tmp_path: Path) -> None:
    run_id, state_dir = _closed_run(tmp_path, refuse=True, want_ok=True)
    play_stops = sum(1 for e in run_payload(run_id, state_dir=state_dir)["timeline"]
                     if e["kind"] == "refused")  # what the Play page counts
    assert play_stops == 1
    assert _score(state_dir, run_id)["gate_stops"] == play_stops


def test_no_refusal_is_zero_gate_stops(tmp_path: Path) -> None:
    run_id, state_dir = _closed_run(tmp_path, refuse=False, want_ok=True)
    assert _score(state_dir, run_id)["gate_stops"] == 0
