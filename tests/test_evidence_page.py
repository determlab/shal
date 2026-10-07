"""shal#380: the evidence page, built from the evidence.json files of a
clean-machine run (dev/e2e/story.py, .github/workflows/e2e-clean-machine.yml).

No hand-written schema (#380's Constraints): every cell here is a copy of the
real evidence.json committed at tests/data/e2e/sample-evidence.json, with
os/python/checks/run_id/run_attempt edited by this file to produce the
pass/fail/missing/retry/non-gating cases (CTO review on #380: macOS never
gates, a retry's pass does not count).
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_EVIDENCE = REPO_ROOT / "tests" / "data" / "e2e" / "sample-evidence.json"
SCRIPT = REPO_ROOT / "dev" / "e2e" / "evidence_page.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evidence_page", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evidence_page"] = mod
    spec.loader.exec_module(mod)
    return mod


evidence_page = _load_module()


def _sample() -> dict:
    doc = json.loads(SAMPLE_EVIDENCE.read_text(encoding="utf-8"))
    doc["run_id"] = "37398801816"
    doc["run_attempt"] = 1
    return doc


def _write_cell(root: Path, name: str, doc: dict | None) -> None:
    cell_dir = root / name
    cell_dir.mkdir(parents=True)
    if doc is not None:
        (cell_dir / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def matrix_dir(tmp_path: Path) -> Path:
    """A 3-cell matrix built from the real sample, never a hand-written one:
    one passing cell (the sample as-is), one with a failed check, and one
    cell whose evidence.json is missing entirely."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()

    passing = _sample()
    _write_cell(ev_dir, "evidence-windows-latest-3.13", passing)

    failing = _sample()
    failing["os"] = "ubuntu-latest"
    failing["python"] = "3.10"
    failing["checks"][0]["result"] = "fail"
    failing["checks"][0]["log"] = "rerun: cd examples && python run_bench.py\nboom"
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", failing)

    _write_cell(ev_dir, "evidence-macos-latest-3.10", None)  # no evidence.json at all

    return ev_dir


def test_page_has_repo_shas_os_python_checks_and_log_reference(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    assert evidence_page.main([str(matrix_dir), "--out", str(out)]) == 1
    page = out.read_text(encoding="utf-8")

    sample = _sample()
    for info in sample["versions"].values():
        assert info["repo"] in page
        assert info["sha"] in page
        assert info["filename"] in page
        assert info["sha256"] in page

    assert "windows-latest" in page and "3.13" in page
    assert "ubuntu-latest" in page and "3.10" in page
    assert sample["run_id"] in page
    assert evidence_page.GATE_RULE in page

    for check in sample["checks"]:
        assert check["id"] in page

    assert 'href="#log-' in page  # a link to each check's own log


def test_failed_check_renders_fail_and_missing_cell_renders_missing(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    evidence_page.main([str(matrix_dir), "--out", str(out)])
    page = out.read_text(encoding="utf-8")

    assert "<td>fail</td>" in page
    assert "evidence-macos-latest-3.10" in page
    assert '<span class="status">missing</span>' in page


def test_json_output_counts_and_page_path(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(matrix_dir), "--out", str(out), "--json"],
        capture_output=True, text=True, check=False,
    )
    doc = json.loads(proc.stdout)
    # CTO review on #380: the macOS cell (missing) does not gate, so the
    # only gating failure is the real one on ubuntu-latest -- exit 1.
    assert proc.returncode == 1
    assert doc == {
        "cells": 3, "passed": 1, "failed": 1, "missing": 1,
        "gating": {"cells": 2, "passed": 1, "failed": 1, "missing": 0},
        # CTO review on #467, must-fix 1: a run whose gating cells actually
        # FAILED is day 0, not day 1 (day 1 would read as progress).
        "day": 0, "gate_met": False,
        "page": str(out), "side_effect": "write",
    }


def test_all_pass_exits_zero_and_escapes_untrusted_text(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    doc = _sample()
    doc["checks"][0]["log"] = "<script>alert(1)</script>"
    _write_cell(ev_dir, "evidence-windows-latest-3.13", doc)
    out = tmp_path / "evidence.html"

    assert evidence_page.main([str(ev_dir), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page


def test_macos_failure_is_non_gating_and_does_not_fail_exit_code(tmp_path):
    """CTO review on #380: a macOS cell never gates the merge decision (same
    ruling as the workflow's own continue-on-error for macos-latest)."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()

    passing = _sample()
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", passing)

    mac_fail = _sample()
    mac_fail["os"] = "macos-latest"
    mac_fail["checks"][0]["result"] = "fail"
    _write_cell(ev_dir, "evidence-macos-latest-3.10", mac_fail)

    out = tmp_path / "evidence.html"
    assert evidence_page.main([str(ev_dir), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "non-gating" in page
    # shal#402: a single clean run is never "gate met" -- the gate is 3
    # daily runs in a row, and with no --history there is nothing to look
    # back on, so this run is always day 1 of 3.
    assert "gate met" not in page
    assert "this run: 1/1 gating cells pass (day 1 of 3)" in page


def test_retry_attempt_is_flagged_and_does_not_silently_pass_as_clean(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    doc = _sample()
    doc["run_attempt"] = 2
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", doc)

    out = tmp_path / "retry.html"
    evidence_page.main([str(ev_dir), "--out", str(out)])
    page = out.read_text(encoding="utf-8")
    assert "attempt 2" in page
    assert "green only on retry, does not count" in page


# --------------------------------------------------------------------------- #
# shal#402: the gate is 3 daily runs in a row, never one run
# --------------------------------------------------------------------------- #

def _four_cell_passing_matrix(root: Path) -> None:
    for os_name, py in [("windows-latest", "3.11"), ("windows-latest", "3.13"),
                        ("ubuntu-latest", "3.11"), ("ubuntu-latest", "3.13")]:
        doc = _sample()
        doc["os"] = os_name
        doc["python"] = py
        _write_cell(root, f"evidence-{os_name}-{py}", doc)


def test_one_passing_run_shows_day_1_of_3_and_never_gate_met(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    out = tmp_path / "evidence.html"
    assert evidence_page.main([str(ev_dir), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "this run: 4/4 gating cells pass (day 1 of 3)" in page
    assert "gate met" not in page


#: "today" for every gate test below (CTO review on #467: never
#: `datetime.now()` -- deterministic, so --date's own 2 preceding calendar
#: days are fixed too).
_TODAY = "2026-01-10"
_YESTERDAY = "2026-01-09"
_DAY_BEFORE = "2026-01-08"
_TODAY_RUN_ID = "37398801816"  # _sample()'s own run_id


def _qualifying(date: str, run_id: str) -> dict:
    return {"date": date, "run_id": run_id, "event": "schedule", "attempt": 1,
           "gating_passed": True}


def test_three_consecutive_daily_passing_scheduled_runs_meet_the_gate(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying(_DAY_BEFORE, "hist-1"), _qualifying(_YESTERDAY, "hist-2"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    assert evidence_page.main(
        [str(ev_dir), "--out", str(out), "--history", str(history),
         "--event", "schedule", "--date", _TODAY]) == 0
    page = out.read_text(encoding="utf-8")
    assert "gate met" in page
    # CTO review on #467 nit: the "this run: N/M" line stays even when met.
    assert "this run: 4/4 gating cells pass (day 3 of 3)" in page


def test_todays_own_run_must_qualify_too_not_only_history(tmp_path):
    """CTO review on #467, must-fix 1: today's own attempt/event were never
    checked before -- only its cell counts. 2 good history days plus a
    retried (attempt 2) today must NOT meet the gate, even though every
    cell happens to have passed on that retry."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)
    # make every gating cell a retry
    for cell_dir in sorted(ev_dir.iterdir()):
        doc = json.loads((cell_dir / "evidence.json").read_text(encoding="utf-8"))
        doc["run_attempt"] = 2
        (cell_dir / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying(_DAY_BEFORE, "hist-1"), _qualifying(_YESTERDAY, "hist-2"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "schedule", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 1 of 3" in page


def test_todays_own_event_must_be_schedule_too(tmp_path):
    """CTO review on #467, must-fix 1: a workflow_dispatch run today, with
    2 good history days, must not meet the gate either."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying(_DAY_BEFORE, "hist-1"), _qualifying(_YESTERDAY, "hist-2"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "workflow_dispatch", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 1 of 3" in page


@pytest.mark.parametrize("bad_entry", [
    {"date": _YESTERDAY, "run_id": "hist-2b", "event": "schedule", "attempt": 2,
     "gating_passed": True},    # retry
    {"date": _YESTERDAY, "run_id": "hist-2b", "event": "schedule", "attempt": 1,
     "gating_passed": False},   # failed
    {"date": _YESTERDAY, "run_id": "hist-2b", "event": "workflow_dispatch", "attempt": 1,
     "gating_passed": True},    # not scheduled
])
def test_a_failed_or_retried_or_manual_run_in_the_3_breaks_the_gate(tmp_path, bad_entry):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    # bad_entry is the most recent of the 2 history days (last = oldest
    # first, per HISTORY_FORMAT), so it is the very first one looked back
    # on and breaks the streak immediately: day stays 1.
    history = tmp_path / "runs.json"
    history.write_text(json.dumps([_qualifying(_DAY_BEFORE, "hist-1"), bad_entry]),
                       encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "schedule", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "this run: 4/4 gating cells pass (day 1 of 3)" in page


def test_a_duplicate_date_in_history_breaks_the_gate(tmp_path):
    """CTO review on #467, must-fix 2: two history entries from the SAME
    day (date never advances) must not be read as 2 distinct daily runs."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying(_YESTERDAY, "hist-1"), _qualifying(_YESTERDAY, "hist-2"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "schedule", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page


def test_a_one_day_gap_in_history_breaks_the_gate(tmp_path):
    """CTO review on #467, must-fix 2: a run from 2 days before yesterday
    (a missed day in between) must not extend the streak."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying("2026-01-06", "hist-1"), _qualifying(_YESTERDAY, "hist-2"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "schedule", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    # the gap is 2 days back, so yesterday (day 2) still counted
    assert "day 2 of 3" in page


def test_a_repeated_run_id_in_history_breaks_the_gate(tmp_path):
    """CTO review on #467, must-fix 2: the same run pasted in twice (or
    reused as today's own run_id) must not be counted as 2 separate days,
    even though its date happens to advance correctly."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _four_cell_passing_matrix(ev_dir)

    history = tmp_path / "runs.json"
    history.write_text(json.dumps([
        _qualifying(_DAY_BEFORE, "same-id"), _qualifying(_YESTERDAY, "same-id"),
    ]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    evidence_page.main([str(ev_dir), "--out", str(out), "--history", str(history),
                        "--event", "schedule", "--date", _TODAY])
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 2 of 3" in page


@pytest.mark.parametrize("bad_field,bad_value", [
    ("gating_passed", "false"),  # a string, not the bool False -- must not be truthy
    ("attempt", True),           # bool is an int subclass in Python -- must not == 1
    ("attempt", 1.0),            # a float -- must not == 1
])
def test_a_malformed_history_field_type_is_rejected_not_silently_truthy(tmp_path, bad_field,
                                                                         bad_value):
    """CTO review on #467, must-fix 3: a malformed or forged history file
    must exit 2, never be coerced into passing."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", _sample())

    entry = _qualifying(_YESTERDAY, "hist-1")
    entry[bad_field] = bad_value
    bad_history = tmp_path / "runs.json"
    bad_history.write_text(json.dumps([entry]), encoding="utf-8")

    out = tmp_path / "evidence.html"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(ev_dir), "--out", str(out),
         "--history", str(bad_history)],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    # CTO review on #467, must-fix 4: names the bad entry's own index, and
    # the next step (fix it, or drop --history).
    assert "entry 0" in proc.stderr
    assert "drop --history" in proc.stderr


def test_help_shows_the_history_option():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--help"],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0
    assert "--history" in proc.stdout


def test_a_bad_history_file_names_the_expected_format(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", _sample())

    bad_history = tmp_path / "runs.json"
    bad_history.write_text("not json", encoding="utf-8")

    out = tmp_path / "evidence.html"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(ev_dir), "--out", str(out),
         "--history", str(bad_history)],
        capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert evidence_page.HISTORY_FORMAT in proc.stderr
    # CTO review on #467 round 2 nit: the next-step line applies to a
    # whole-file error too, not only a bad entry.
    assert "drop --history" in proc.stderr


# --------------------------------------------------------------------------- #
# CTO review on #467 round 2: 3 more ways the gate check could be fooled,
# found after round 1 shipped -- zero gating cells, an unproven attempt, and
# cells stitched together from more than one run
# --------------------------------------------------------------------------- #

def _good_history() -> str:
    return json.dumps([_qualifying(_DAY_BEFORE, "hist-1"), _qualifying(_YESTERDAY, "hist-2")])


def _run_with_history(ev_dir: Path, out: Path, history: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(ev_dir), "--out", str(out),
         "--history", str(history), "--event", "schedule", "--date", _TODAY],
        capture_output=True, text=True, check=False)


def test_an_empty_evidence_dir_does_not_meet_the_gate(tmp_path):
    """must-fix 1: 0 gating cells used to count as 'all green' -- an empty
    `ev/` (gh run download got nothing, or the artifacts expired)."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    history = tmp_path / "runs.json"
    history.write_text(_good_history(), encoding="utf-8")

    out = tmp_path / "evidence.html"
    proc = _run_with_history(ev_dir, out, history)
    assert proc.returncode == 0  # 0 gating cells: nothing failed or is missing either
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 0 of 3" in page


def test_a_macos_only_dir_does_not_meet_the_gate(tmp_path):
    """must-fix 1, the other real-world shape: every cell present is
    non-gating, so gating["cells"] is still 0."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    mac = _sample()
    mac["os"] = "macos-latest"
    _write_cell(ev_dir, "evidence-macos-latest-3.10", mac)
    history = tmp_path / "runs.json"
    history.write_text(_good_history(), encoding="utf-8")

    out = tmp_path / "evidence.html"
    proc = _run_with_history(ev_dir, out, history)
    assert proc.returncode == 0
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 0 of 3" in page


def test_a_missing_run_attempt_does_not_count_as_attempt_1(tmp_path):
    """must-fix 2: `_is_retry(None)` is False, so a cell with no
    `run_attempt` key used to read as a clean attempt 1."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    doc = _sample()
    del doc["run_attempt"]
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", doc)
    history = tmp_path / "runs.json"
    history.write_text(_good_history(), encoding="utf-8")

    out = tmp_path / "evidence.html"
    _run_with_history(ev_dir, out, history)
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 1 of 3" in page


def test_gating_cells_from_different_runs_do_not_anchor_a_streak(tmp_path):
    """must-fix 3: run_id was the first cell's that had one, with the rest
    never compared -- a stale or partial re-run could stitch a false
    'today' together from 2 different runs."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    a = _sample()
    a["run_id"] = "102"
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", a)
    b = _sample()
    b["os"] = "windows-latest"
    b["run_id"] = "999"
    _write_cell(ev_dir, "evidence-windows-latest-3.10", b)
    history = tmp_path / "runs.json"
    history.write_text(_good_history(), encoding="utf-8")

    out = tmp_path / "evidence.html"
    _run_with_history(ev_dir, out, history)
    page = out.read_text(encoding="utf-8")
    assert "gate met" not in page
    assert "day 1 of 3" in page
