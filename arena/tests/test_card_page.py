"""issue #315 Done-when: result card, replay and rack page."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from shal_arena.card_page import card_data, render_card, render_rack, setup_yaml, write_card
from shal_arena.errors import MeasurementFailed
from shal_arena.fault import realized_fault
from shal_arena.loader import load_task
from shal_arena.runner import (
    _import_driver_file,
    answer,
    check_instrument_driver,
    drive_input,
    start_run,
    take_measurement,
)
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_DRIVER, SAMPLE_TASK


def _seed_for(fault_id: str) -> int:
    card = load_task(SAMPLE_TASK).card
    return next(s for s in range(300) if realized_fault(card, s).fault_id == fault_id)


def _play(tmp_path: Path, fault_id: str = "low_voltage", close: bool = True):
    state_dir = tmp_path / "state"
    run_id = start_run(SAMPLE_TASK, seed=_seed_for(fault_id), state_dir=state_dir)["run_id"]
    check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    try:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:
        pass
    store = RunStore(state_dir)
    if close:
        answer(run_id, fault_id, state_dir=state_dir)
        record = json.loads(store.record_path(run_id).read_text(encoding="utf-8"))
        score = json.loads(store.score_path(run_id).read_text(encoding="utf-8"))
        return store, run_id, record, score
    state = json.loads(store._public_path(run_id).read_text(encoding="utf-8"))
    return store, run_id, state, None


def test_card_from_real_record_and_score(tmp_path: Path) -> None:
    store, run_id, record, score = _play(tmp_path)
    out = write_card(store.record_path(run_id), store.score_path(run_id), tmp_path / "card.html")
    page = out.read_text(encoding="utf-8")
    assert "1 of 1 faults caught. 0 false fails." in page
    assert "Replay" in page and "Copy result" in page and "Save as image" in page
    assert f"replay · simulated · seed {score['seed']}" in page
    assert score["record_sha256"][:12] in page and score["record_sha256"] not in page
    assert score["fault_type"] in page and score["game_version"] in page
    assert "1200" in page and "630" in page


def test_turns_equal_check_measure_drive_and_refused_counted(tmp_path: Path) -> None:
    _, _, record, score = _play(tmp_path)
    expected = sum(1 for c in record["calls"] if c["kind"] in ("check", "measure", "drive"))
    assert expected == 4 == score["turns"]
    assert card_data(record, score)["turns"] == expected
    # a refused drive is shown in red and still one turn; the answer is not a turn
    for c in record["calls"]:
        if c["kind"] == "drive":
            c["refused"] = True
    page = render_card(record, score)
    assert card_data(record, score)["turns"] == expected
    assert f"{expected} turns" in page
    assert 'class="red"' in page


def test_false_fails_said_openly(tmp_path: Path) -> None:
    _, _, record, score = _play(tmp_path, "ok", close=False)
    record = dict(record, closed_at="2026-01-01T00:00:00Z", given="low_voltage", correct=False)
    score = {"task_id": "t", "seed": 1, "fault_type": "ok", "faults_total": 1,
             "faults_caught": 0, "false_fails": 1, "error_fail_correct": 0, "duration_s": 1.0,
             "turns": 4, "gate_stops": 0, "schema_version": 1, "game_version": "0.4.0",
             "record_sha256": "a" * 64}
    assert "1 false fail" in card_data(record, score)["headline"]
    assert "0 of 1 faults caught. 1 false fails." in render_card(record, score)


def test_card_html_has_only_the_repo_link_as_http(tmp_path: Path) -> None:
    store, run_id, _, _ = _play(tmp_path)
    page = write_card(store.record_path(run_id), store.score_path(run_id),
                      tmp_path / "card.html").read_text(encoding="utf-8")
    hits = [ln for ln in page.splitlines() if "http" in ln]
    assert len(hits) == 1 and "github.com/determlab/shal" in hits[0]
    assert all("github.com/determlab/shal" in m for m in re.findall(r"https?://\S+", page))
    assert "<script src" not in page and "@import" not in page and "xmlns" not in page
    assert page.rstrip().endswith("</html>")


@pytest.mark.parametrize("fault_id", ["low_voltage", "noise", "open", "ok"])
# the in-progress state json is what a not-yet-closed run has on disk
def test_card_before_run_end_names_no_fault(fault_id: str, tmp_path: Path) -> None:
    _, _, state, _ = _play(tmp_path, fault_id, close=False)
    page = render_card(state, None)
    data = card_data(state, None)
    assert not data["closed"] and "fault_type" not in data
    for word in ("low_voltage", "noise", "open", "fault_type", "shift_v", "ripple"):
        assert word not in page
        assert word not in json.dumps(data)


def test_card_data_is_readable_without_the_page(tmp_path: Path) -> None:
    store, run_id, record, score = _play(tmp_path)
    d = card_data(record, score)
    json.dumps(d)
    assert d["headline"] == "1 of 1 faults caught. 0 false fails."
    assert d["fault_type"] == score["fault_type"] and d["seed"] == score["seed"]
    assert [c["kind"] for c in d["calls"]] == ["check", "check", "drive", "measure"]
    assert d["bottom_line"] == f"replay · simulated · seed {score['seed']}"


def test_rack_page_tiles_and_issue_link(tmp_path: Path) -> None:
    page = render_rack()
    assert page.count("Request a driver") == 2
    assert "My instrument is not here" in page
    assert page.count('draggable="true"') == 2
    assert "labels=driver-request" in page
    assert "setup.yaml" in page and "Download" in page and "Copy" in page


def test_generated_setup_yaml_loads_in_shal(tmp_path: Path) -> None:
    from shal import load

    _import_driver_file(PASSING_DRIVER)
    _import_driver_file(PASSING_DMM_DRIVER)
    text = setup_yaml([
        {"id": "psu0", "driver": "arena,bench-psu1"},
        None,
        {"id": "dmm0", "driver": "arena,bench-dmm1"},
    ])
    path = tmp_path / "setup.yaml"
    path.write_text(text, encoding="utf-8")
    with load(str(path)) as hal:
        assert hal.tool_schemas()
    assert setup_yaml([]).splitlines()[-1] == "    children: {}"
