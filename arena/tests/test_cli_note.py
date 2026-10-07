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

import pytest

from shal_arena.cli import NOTE_MAX_CHARS, _redact_cli_line
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
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


def test_a_multi_line_note_with_repeated_spaces_comes_back_byte_identical() -> None:
    """CTO review item 2: "never shorten, rewrite or hide the note" -- a note
    with no secret in it keeps every newline and run of spaces."""
    written = "line one\n  indented   two\t\tand a tab\n"
    _argv, _payload, _text, note = _redact_cli_line([], None, None, written)
    assert note == written


def test_a_token_in_a_multi_line_note_is_redacted_and_the_layout_kept() -> None:
    _argv, _payload, _text, note = _redact_cli_line(
        [], None, None, "first\n  re-ran with --token   abc123\n done")
    assert note == "first\n  re-ran with --token   ***\n done"


@pytest.mark.parametrize("form", ["split", "equals", "abbreviated"])
def test_the_note_value_in_argv_is_redacted_too(form: str) -> None:
    """CTO review item 1: the note is also IN argv, as the --note value;
    `redact_secret_args` alone never looks inside it."""
    raw = "checked http://user:s3cr3t@example.invalid and --token abc123"
    tail = {"split": ["--note", raw], "equals": [f"--note={raw}"],
            "abbreviated": ["--no", raw]}[form]
    argv, _payload, _text, note = _redact_cli_line(["measure", "r1", "--json", *tail],
                                                   None, None, raw)
    dumped = json.dumps(argv)
    assert "abc123" not in dumped and "s3cr3t" not in dumped
    assert note in argv[-1]
    assert argv[:3] == ["measure", "r1", "--json"]


def test_no_note_stays_none() -> None:
    _argv, _payload, _text, note = _redact_cli_line(["--seed", "9"], None, None, None)
    assert note is None


# --------------------------------------------------------------------------- #
# end-to-end: real CLI subprocesses, the run's own `<run>.cli.jsonl`.
# --------------------------------------------------------------------------- #

_NOTED_SUBCOMMANDS = ["run", "check", "measure", "drive", "call", "answer", "replay"]


def _subcommand_argv(command: str, state_dir: Path) -> tuple[list[str], str]:
    """One real invocation of `command` (without --note), on a run prepared
    for it, and the run id its cli.jsonl line lands under."""
    if command == "run":
        return ["run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json"], ""
    run_id = _start_run(state_dir)
    common = ["--state-dir", str(state_dir), "--json"]
    if command == "replay":
        assert _run_cli("answer", run_id, "ok", *common).returncode == 0
    tail = {
        "check": [run_id, "dmm0", str(PASSING_DMM_DRIVER)],
        "measure": [run_id, "dmm0", str(PASSING_DMM_DRIVER)],
        "drive": [run_id, "psu0", "5.0"],
        "call": [run_id, "dmm0", str(PASSING_DMM_DRIVER), "measure_voltage"],
        "answer": [run_id, "ok"],
        "replay": [run_id, "--out", str(state_dir / "card.html")],
    }[command]
    return [command, *tail, *common], run_id


@pytest.mark.parametrize("command", _NOTED_SUBCOMMANDS)
@pytest.mark.parametrize("with_note", [True, False])
def test_every_run_subcommand_logs_its_note_or_null(tmp_path: Path, command: str,
                                                    with_note: bool) -> None:
    """DoD box 1, for every subcommand that names a run."""
    state_dir = tmp_path / "state"
    argv, run_id = _subcommand_argv(command, state_dir)
    proc = _run_cli(*argv, *(["--note", "x"] if with_note else []))
    assert proc.returncode == 0, proc.stderr
    run_id = run_id or json.loads(proc.stdout)["run_id"]
    line = _lines(state_dir, run_id)[-1]
    assert line["argv"][0] == command
    assert line["note"] == ("x" if with_note else None)


@pytest.mark.parametrize("argv", [["rack", "--out", "r.html"], ["setup-yaml", "dmm"],
                                  ["bench", "--runs", "1"]])
def test_a_command_with_no_run_rejects_note(tmp_path: Path, argv: list[str]) -> None:
    """CTO review item 4: on a command that never names a run, --note would
    be accepted and silently dropped -- argparse refuses it instead."""
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *argv, "--note", "my rack note"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, cwd=tmp_path)
    assert proc.returncode == 2
    assert "--note" in proc.stderr
    assert not (tmp_path / "r.html").exists()


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
    """DoD box 2: `check` is deterministic, so its stdout is compared byte
    for byte, with and without --json."""
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)

    for fmt in (["--json"], []):
        argv = ["check", run_id, "dmm0", str(PASSING_DMM_DRIVER), "--state-dir",
                str(state_dir), *fmt]
        plain = _run_cli(*argv)
        noted = _run_cli(*argv, "--note", "second look")
        assert plain.returncode == noted.returncode == 0
        assert plain.stdout == noted.stdout
        assert plain.stderr == noted.stderr


def test_note_never_changes_measures_output_shape_or_exit_code(tmp_path: Path) -> None:
    """The second case: `measure` reads a drifting sim model, so only the
    reading itself may differ call to call."""
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

    raw = "checked http://user:s3cr3t@example.invalid and --token abc123"
    for form in (["--note", raw], [f"--note={raw}"]):
        proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                        "--state-dir", str(state_dir), "--json", *form)
        assert proc.returncode == 0, proc.stderr

    # the whole file, not only `note`: the same text also sits in `argv`
    on_disk = RunStore(state_dir).cli_log_path(run_id).read_text(encoding="utf-8")
    assert "s3cr3t" not in on_disk
    assert "abc123" not in on_disk
    assert [ln["note"] for ln in _lines(state_dir, run_id)[-2:]] == [
        "checked http://example.invalid and --token ***"] * 2


def test_a_multi_line_note_is_logged_byte_identical(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    written = "line one\n  indented   two"
    proc = _run_cli("check", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", written)
    assert proc.returncode == 0, proc.stderr
    line = _lines(state_dir, run_id)[-1]
    assert line["note"] == written
    assert line["argv"][-1] == written


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


@pytest.mark.parametrize("fmt", [["--json"], []])
def test_has_reading_is_the_same_with_and_without_json(tmp_path: Path,
                                                       fmt: list[str]) -> None:
    """CTO review item 3: a plain-text `measure` logs `json: null` but did
    take a reading -- `has_reading` follows the call, not the output format."""
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), *fmt, "--note", "rail looks low")
    assert proc.returncode == 0, proc.stderr
    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["notes"][0]["has_reading"] is True
    assert _RECORD_LINE in _render_with_node(payload)["notes-section"]["html"]


def test_a_truncated_cli_log_line_does_not_take_the_page_down(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _start_run(state_dir)
    proc = _run_cli("check", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                    "--state-dir", str(state_dir), "--json", "--note", "kept")
    assert proc.returncode == 0, proc.stderr
    path = RunStore(state_dir).cli_log_path(run_id)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"time": "2026-10-07T00:00:00Z", "argv": ["measure"], "no')
    assert [n["note"] for n in run_payload(run_id, state_dir=state_dir)["notes"]] == ["kept"]


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

    # export of the closed run: the same note embedded through safe_json,
    # with no raw "</script>" or live "<img" anywhere in the file.
    assert _run_cli("answer", run_id, "ok", "--state-dir", str(state_dir),
                    "--json").returncode == 0
    export_doc = build_export(run_id, state_dir=state_dir)
    assert "</script><img" not in export_doc
    assert "<img" not in export_doc
    assert export_doc.count("</script>") == export_doc.count("<script")
