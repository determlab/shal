"""issue #470: an optional ``--note`` on every ``shal-arena`` command that
names a run -- saved next to that call in ``<run>.cli.jsonl``, shown apart
from readings in the `ui` page. ``--note`` never changes what the command
itself does or returns; it only adds to the log line."""
from __future__ import annotations

import html
import json
import subprocess
import sys
from pathlib import Path

from shal_arena.cli import NOTE_MAX_CHARS, _redact_cli_line
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload
from shal_arena.ui.page import render_watch_page

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK
from .test_ui_timeline_no_exchange import _render_with_node


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def _lines(state_dir: Path, run_id: str) -> list[dict]:
    path = RunStore(state_dir).cli_log_path(run_id)
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


def _start_run(state_dir: Path) -> str:
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    return json.loads(run_proc.stdout)["run_id"]


# --------------------------------------------------------------------------- #
# redaction -- the same URL/secret rule `text`/`json` already get.
# --------------------------------------------------------------------------- #

def test_a_url_in_a_note_is_redacted() -> None:
    _argv, _payload, _text, note = _redact_cli_line(
        [], None, None, "rail looks ok at http://user:s3cr3t@example.invalid/path")
    assert "s3cr3t" not in note
    assert "http://example.invalid/path" in note


def test_a_token_flag_echoed_in_a_note_is_redacted() -> None:
    _argv, _payload, _text, note = _redact_cli_line(
        [], None, None, "re-ran with --token abc123 after the first try failed")
    assert "abc123" not in note
    assert "--token ***" in note


def test_a_note_with_no_secret_stays_readable() -> None:
    _argv, _payload, _text, note = _redact_cli_line(
        [], None, None, "measured 3.30 V, looks nominal")
    assert note == "measured 3.30 V, looks nominal"


def test_no_note_stays_none() -> None:
    _argv, _payload, _text, note = _redact_cli_line(["--seed", "9"], None, None, None)
    assert note is None


# --------------------------------------------------------------------------- #
# end-to-end: real CLI subprocesses, the run's own `<run>.cli.jsonl`.
# --------------------------------------------------------------------------- #

def test_a_note_is_saved_next_to_the_call_it_was_given_on(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", "looks nominal")
    assert proc.returncode == 0, proc.stderr

    lines = _lines(state_dir, run_id)
    measure_line = lines[-1]
    assert measure_line["note"] == "looks nominal"
    assert "measure" in measure_line["argv"]


def test_a_call_without_note_logs_null(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json")
    assert proc.returncode == 0, proc.stderr
    assert _lines(state_dir, run_id)[-1]["note"] is None


def test_note_never_changes_the_commands_own_stdout_or_exit_code(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    plain = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                     "--state-dir", str(state_dir), "--json")
    noted = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                     "--state-dir", str(state_dir), "--json", "--note", "second look")
    assert plain.returncode == noted.returncode == 0
    plain_doc = json.loads(plain.stdout)
    noted_doc = json.loads(noted.stdout)
    for doc in (plain_doc, noted_doc):
        doc.pop("reading", None)  # the sim model drifts call to call; not what this checks
    assert plain_doc == noted_doc


def test_a_url_or_token_in_a_logged_note_is_redacted_end_to_end(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json",
                    "--note", "checked http://user:s3cr3t@example.invalid and --token abc123")
    assert proc.returncode == 0, proc.stderr

    note = _lines(state_dir, run_id)[-1]["note"]
    assert "s3cr3t" not in note
    assert "abc123" not in note


def test_a_note_over_the_limit_errors_and_runs_nothing(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    before = _lines(state_dir, run_id)

    long_note = "x" * (NOTE_MAX_CHARS + 1)
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", long_note)
    assert proc.returncode != 0
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False
    assert str(NOTE_MAX_CHARS) in doc["error"]["message"]
    assert doc["error"]["fix"]

    assert _lines(state_dir, run_id) == before  # no new line for the rejected call


def test_a_note_at_exactly_the_limit_is_accepted(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    note = "x" * NOTE_MAX_CHARS
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", note)
    assert proc.returncode == 0, proc.stderr
    assert _lines(state_dir, run_id)[-1]["note"] == note


# --------------------------------------------------------------------------- #
# the view: notes apart from readings, never inside a measurement row.
# --------------------------------------------------------------------------- #

def test_payload_lists_notes_separately_from_the_timeline(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", "looks nominal")
    assert proc.returncode == 0, proc.stderr

    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["notes"] == [
        {"command": "measure", "note": "looks nominal", "has_reading": True,
         "time": payload["notes"][0]["time"]}]
    for row in payload["timeline"]:
        assert "note" not in row  # a note is never folded into a measurement row


# --------------------------------------------------------------------------- #
# the view: the CMO's exact heading, and the fixed record/claim line under
# every note whose own call also carried a reading -- never under one that
# didn't, and never auto-detecting agreement/disagreement either way.
# --------------------------------------------------------------------------- #

_HEADING = "The agent's own note (its words, not checked)"
_RECORD_LINE = "The record shows the reading above. The note is the agent's claim."


def test_the_heading_and_record_line_appear_verbatim_when_a_reading_exists(
        tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json",
                    "--note", "rail looks low, checking again")
    assert proc.returncode == 0, proc.stderr

    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)
    notes_html = elements["notes-section"]["html"]
    assert _HEADING in notes_html
    assert "rail looks low, checking again" in notes_html
    assert _RECORD_LINE in notes_html


def test_the_record_line_appears_even_when_the_note_agrees_with_the_reading(
        tmp_path: Path) -> None:
    """The CTO's call: no conflict detection -- the fixed line never reads
    the note's own text, so it shows up the same whether the note agrees
    with the reading or not."""
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json",
                    "--note", "looks nominal, matches what I expected")
    assert proc.returncode == 0, proc.stderr

    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)
    assert _RECORD_LINE in elements["notes-section"]["html"]


def test_the_record_line_never_appears_under_a_note_with_no_reading(
        tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    # `check` never measures -- its own --json payload carries no `reading`
    proc = _run_cli("check", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json",
                    "--note", "driver looks right to me")
    assert proc.returncode == 0, proc.stderr

    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["notes"][0]["has_reading"] is False
    elements = _render_with_node(payload)
    notes_html = elements["notes-section"]["html"]
    assert _HEADING in notes_html
    assert "driver looks right to me" in notes_html
    assert _RECORD_LINE not in notes_html


def test_a_note_with_script_tags_stays_inert_live_and_in_the_export(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    payload_note = "</script><img src=x onerror=alert(1)>"
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", payload_note)
    assert proc.returncode == 0, proc.stderr

    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["notes"][0]["note"] == payload_note  # kept whole for the live `textContent`
                                                          # renderer -- never escaped on the wire

    # live: set through textContent (renderNotes), never innerHTML, on the
    # note's own path -- the rendered HTML holds the ESCAPED form, never a
    # live, interpretable "</script><img ...>".
    elements = _render_with_node(payload)
    assert "</script><img" not in elements["notes-section"]["html"]
    assert html.unescape(elements["notes-section"]["html"]).count(payload_note) == 1

    # export: embedded through safe_json, with no raw "</script>" anywhere.
    html_doc = render_watch_page(run_id, payload)
    assert "</script><img" not in html_doc
