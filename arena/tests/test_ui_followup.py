"""issue #427 (demo follow-up to #406): the CMO problem text is on every
page, `drivers` round-trips into the embedded payload and is never
interpreted as markup, `rails`/`level` are in the payload for the answer
sentence, and the static shell has the separate containers the
"Two more checks, scripted" section and the driver-code fold need."""
from __future__ import annotations

from pathlib import Path

from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.export import build_export
from shal_arena.ui.page import PROBLEM_LINES, render_watch_page

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

_BASE_PAYLOAD = {
    "run_id": "run-x", "task_id": "t", "title": "T", "question": "Q",
    "level": "easy", "status": "open", "closed": False, "turns": 0,
    "instruments": [{"address": "psu0", "case": None, "drives": "card.vin", "probe": None},
                     {"address": "dmm0", "case": None, "drives": None, "probe": "card.tp_3v3"}],
    "rails": [{"label": "tp_3v3", "nominal_v": 3.3, "tol_pct": 5, "lo": 3.135, "hi": 3.465}],
    "tiles": {}, "card": {"applied": {}, "destroyed": False},
    "timeline": [], "record": None, "score": None, "drivers": {},
}


def test_problem_lines_are_on_the_static_shell() -> None:
    html = render_watch_page("run-x", _BASE_PAYLOAD)
    for line in PROBLEM_LINES:
        assert line in html


def test_shell_has_the_driver_code_and_scripted_containers() -> None:
    html = render_watch_page("run-x", _BASE_PAYLOAD)
    assert '<div id="driver-code-section">' in html
    assert '<div id="scripted-section">' in html
    assert '<div id="plain-line" class="plain-line">' in html


def test_payload_carries_level_and_rails_for_the_answer_sentence() -> None:
    payload = dict(_BASE_PAYLOAD)
    html = render_watch_page("run-x", payload)
    assert '"level": "easy"' in html or '"level":"easy"' in html
    assert "tp_3v3" in html


def test_a_hostile_driver_code_cannot_break_out_of_the_script_tag() -> None:
    """The driver's own source is arbitrary text an agent wrote -- it is
    rendered client side with `textContent` (never `innerHTML`), but the
    embedded JSON itself must still survive a `</script>` breakout attempt,
    same as a hostile `record.given` (test_ui_api.py's own check)."""
    payload = dict(_BASE_PAYLOAD)
    hostile = "def read():\n    pass  # </script><script>window.pwned=1</script>"
    payload["drivers"] = {"dmm": {"lines": 2, "code": hostile}}
    html = render_watch_page("run-x", payload)
    assert "</script><script>window.pwned" not in html
    assert "\\u003c/script>\\u003cscript>window.pwned" in html
    assert html.count("<script") == 2
    assert html.count("</script>") == 2


def test_a_hostile_driver_code_is_never_executed_as_html_on_export() -> None:
    payload = dict(_BASE_PAYLOAD)
    payload["closed"] = True
    payload["status"] = "closed"
    payload["record"] = {"given": "ok", "correct": True, "disqualified": False}
    hostile = "<img src=x onerror=alert(1)>"
    payload["drivers"] = {"psu": {"lines": 1, "code": hostile}}
    from shal_arena.ui.export import render_export_page

    html = render_export_page(payload)
    assert "<img src=x onerror" not in html


def test_reading_kind_is_logged_only_on_a_successful_read_and_shows_up_in_the_timeline(
        tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    from shal_arena.ui.data import run_payload
    payload = run_payload(run_id, state_dir=tmp_path)
    readings = [e for e in payload["timeline"] if e["kind"] == "reading"]
    assert len(readings) == 1
    assert readings[0]["address"] == "dmm0"
    assert isinstance(readings[0]["detail"]["value"], float)


def test_drivers_round_trip_through_the_export(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    drivers = {"dmm": {"lines": 3, "code": "def read():\n    return 3.3\n"}}
    html = build_export(run_id, state_dir=tmp_path, drivers=drivers)
    assert "def read():" in html
    assert '"dmm"' in html
