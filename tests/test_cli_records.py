"""`shal records` — read the record store from the CLI (issue #251).

Read-only (`side_effect: none`): no topology, just `record.read()` over
`DIR/records.db`. Every test runs the real command in a fresh process (as
`test_cli_check.py` does), so the exit code and the JSON on stdout are what an
agent actually sees.
"""
import json
import subprocess
import sys

import pytest

from shal.record import Record, Step, write


def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    """Run the real command in a fresh process (not an import of `main`)."""
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def _record(record_id: str, *, unit: str, station: str = "st1", sequence: str = "seq1",
            started: str, verdict: str = "pass", record_version: int | None = None) -> Record:
    kw = {} if record_version is None else {"record_version": record_version}
    return Record(
        record=record_id, unit=unit, station=station, sequence=sequence,
        sequence_version="1", setup="bench.yaml", setup_version="1", runner="script",
        started=started, ended=started,
        steps=[Step(name="only", verdict=verdict if verdict != "aborted" else "pass")],
        **kw,
    )


# Two records, deliberately out of insertion order, so "newest first" and
# "--last" are only correct if the command sorts by `started`, not by write order.
_OLD = _record("rec-old", unit="unit-a", verdict="pass", started="2026-09-28T10:00:00Z")
_NEW = _record("rec-new", unit="unit-b", verdict="fail", started="2026-09-28T11:00:00Z")


@pytest.fixture
def store(tmp_path):
    write(_OLD, tmp_path)
    write(_NEW, tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# no store: exit 1, a structured NoStore error naming the fix
# --------------------------------------------------------------------------- #

def test_no_store_exits_1_and_names_the_fix_on_stderr(tmp_path):
    r = _shal("records", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 1
    assert "no records.db in" in r.stderr
    assert "shal docs --sample jig --to DIR" in r.stderr
    assert r.stdout == ""


def test_no_store_json_shape(tmp_path):
    r = _shal("records", str(tmp_path), "--json", cwd=tmp_path)
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload == {
        "ok": False,
        "error": {
            "type": "NoStore",
            "message": f"no records.db in {tmp_path}",
            "fix": "run a test with pytest-shal or the jig sample "
                   "('shal docs --sample jig --to DIR'), or pass the directory "
                   "that holds records.db",
        },
    }


# --------------------------------------------------------------------------- #
# text: one line per record, newest first
# --------------------------------------------------------------------------- #

def test_text_output_is_newest_first(store):
    r = _shal("records", str(store), cwd=store)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines == [
        "2026-09-28T11:00:00Z  unit-b  st1  seq1  fail  rec-new",
        "2026-09-28T10:00:00Z  unit-a  st1  seq1  pass  rec-old",
    ]


def test_default_dir_is_the_current_directory(store):
    r = _shal("records", cwd=store)
    assert r.returncode == 0, r.stderr
    assert "rec-new" in r.stdout and "rec-old" in r.stdout


# --------------------------------------------------------------------------- #
# filters: --unit, --verdict; and --last
# --------------------------------------------------------------------------- #

def test_filter_by_unit(store):
    r = _shal("records", str(store), "--unit", "unit-a", cwd=store)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines() == [
        "2026-09-28T10:00:00Z  unit-a  st1  seq1  pass  rec-old",
    ]


def test_filter_by_verdict(store):
    r = _shal("records", str(store), "--verdict", "fail", cwd=store)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines() == [
        "2026-09-28T11:00:00Z  unit-b  st1  seq1  fail  rec-new",
    ]


def test_filter_with_no_match_is_still_exit_0(store):
    r = _shal("records", str(store), "--unit", "nobody", "--json", cwd=store)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["records"] == []


def test_last_1_keeps_only_the_most_recent(store):
    r = _shal("records", str(store), "--last", "1", cwd=store)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines() == [
        "2026-09-28T11:00:00Z  unit-b  st1  seq1  fail  rec-new",
    ]


# --------------------------------------------------------------------------- #
# --json: {"ok": true, "store": ..., "records": [<record, as to_json decodes it>]}
# --------------------------------------------------------------------------- #

def test_json_shape(store):
    r = _shal("records", str(store), "--json", cwd=store)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert payload["store"].endswith("records.db")
    ids = [rec["record"] for rec in payload["records"]]
    assert ids == ["rec-new", "rec-old"]         # newest first, same as text
    newest = payload["records"][0]
    assert newest["unit"] == "unit-b"
    assert newest["station"] == "st1"
    assert newest["sequence"] == "seq1"
    assert newest["verdict"] == "fail"
    assert newest["started"] == "2026-09-28T11:00:00Z"
    assert "skipped" not in payload              # only present with --skip-newer


# --------------------------------------------------------------------------- #
# a newer record_version: refused by default, skippable with --skip-newer
# --------------------------------------------------------------------------- #

@pytest.fixture
def store_with_newer(tmp_path):
    write(_OLD, tmp_path)
    newer = _record("rec-newer-version", unit="unit-c", verdict="pass",
                    started="2026-09-28T12:00:00Z", record_version=99)
    write(newer, tmp_path)
    return tmp_path


def test_a_newer_record_refuses_the_whole_read_by_default(store_with_newer):
    r = _shal("records", str(store_with_newer), cwd=store_with_newer)
    assert r.returncode == 1
    assert "newer than this SHAL reads" in r.stderr
    assert r.stdout == ""


def test_a_newer_record_refuses_in_json_too(store_with_newer):
    r = _shal("records", str(store_with_newer), "--json", cwd=store_with_newer)
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["ok"] is False
    assert "newer than this SHAL reads" in payload["error"]


def test_skip_newer_returns_the_readable_records_and_lists_the_skipped_one(
        store_with_newer):
    r = _shal("records", str(store_with_newer), "--skip-newer", "--json",
              cwd=store_with_newer)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["ok"] is True
    assert [rec["record"] for rec in payload["records"]] == ["rec-old"]
    assert payload["skipped"] == [{"id": "rec-newer-version", "record_version": 99}]


def test_skip_newer_text_mode_prints_the_skipped_id(store_with_newer):
    r = _shal("records", str(store_with_newer), "--skip-newer", cwd=store_with_newer)
    assert r.returncode == 0, r.stderr
    assert "rec-old" in r.stdout
    assert "rec-newer-version" in r.stderr
