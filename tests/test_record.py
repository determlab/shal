"""The record (D22, `record.md`): one shape, two stores, the YAML wins.

The load-bearing test here is the round-trip — a record written by `write()` must
come back *identical* from the SQLite index and from the YAML audit copy, and the
two must be identical to each other. That is what lets Predictor train on records
a station wrote a year ago and the floor screen index the same rows.
"""
import json
import re
import sqlite3

import pytest
import yaml

# `_read_db` / `_read_yaml` are private on purpose: `record.md` §3 names only
# `write(record, store)` and `read(store)`, and the DoD's "byte-identical from
# both stores" cannot be shown through a reader that already applies the
# YAML-wins rule. They stay underscored until the spec names a public pair.
from shal.record import (
    RECORD_VERSION,
    Abort,
    Limits,
    Measurement,
    Record,
    RecordError,
    Step,
    _read_db,
    _read_yaml,
    db_path,
    read,
    to_json,
    to_yaml,
    write,
    yaml_path,
)

# `record.md` §2's own example, plus every awkward shape the round-trip must
# survive: a one-sided limit, a float that is not exactly representable, a
# negative value, `None` inside the pass-through `calls` list, and nesting.
FULL = Record(
    record="rec-20260909T143022-8f1a2c",
    unit="SN-000417",
    station="line2-st4",
    sequence="psu-bringup",
    sequence_version="3f2a9c1",
    firmware="fw-1.4.2+b117",
    setup="bench.yaml",
    setup_version="91efa96",
    runner="pytest",
    started="2026-09-09T14:30:22Z",
    ended="2026-09-09T14:31:07Z",
    steps=[
        Step(
            name="measure_vout",
            verdict="pass",
            measurements=[
                Measurement(name="vout", value=4.98, unit="V",
                            limits=Limits(min=4.9, max=5.1), passed=True),
                Measurement(name="iout", value=-0.1234567890123, unit="A",
                            limits=Limits(), passed=True),
            ],
        ),
        Step(
            name="ripple",
            verdict="fail",
            measurements=[
                Measurement(name="ripple_pp", value=61.2, unit="mV",
                            limits=Limits(max=50), passed=False),
            ],
        ),
    ],
    calls=[
        {"capability": "psu.set_voltage", "side_effect": "write",
         "shal_txn": "a1b2", "result": "ok"},
        {"capability": "psu.read_voltage", "side_effect": "read",
         "shal_txn": "c3d4", "result": None, "args": {"channel": 1, "sweep": [1, 2.5]}},
    ],
)

MINIMAL = Record(
    record="rec-20260909T090000-000001",
    unit="bench",                      # §2: never blank
    station="bench-hemi",
    sequence="tests/test_psu.py::test_vout",
    sequence_version="0000000",
    setup="bench.yaml",
    setup_version="91efa96",
    runner="pytest",
    started="2026-09-09T09:00:00Z",
    ended="2026-09-09T09:00:01Z",
)


# --------------------------------------------------------------------------- #
# DoD: written once, read back byte-identical from BOTH stores
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("original", [FULL, MINIMAL], ids=["full", "minimal"])
def test_round_trip_is_byte_identical_from_both_stores(tmp_path, original):
    write(original, tmp_path)

    from_yaml = _read_yaml(tmp_path, original.record)
    from_db = _read_db(tmp_path, original.record)

    # 1. Both stores gave back a record equal to the one that went in. Frozen
    #    dataclasses compare by value all the way down, so this covers every
    #    field, every step, every measurement and every pass-through call.
    assert from_yaml == original
    assert from_db == original
    assert from_yaml == from_db

    # 2. Byte-identical, not merely equal: re-serialising what each store gave
    #    back reproduces the same bytes. This is what catches a float that lost
    #    a digit, a key that moved, or a `None` that became "None".
    assert to_yaml(from_yaml).encode() == to_yaml(original).encode()
    assert to_yaml(from_db).encode() == to_yaml(original).encode()
    assert to_json(from_yaml).encode() == to_json(original).encode()
    assert to_json(from_db).encode() == to_json(original).encode()

    # 3. And the bytes actually on disk are those bytes.
    on_disk = yaml_path(tmp_path, original.record).read_bytes()
    assert on_disk == to_yaml(original).encode()
    assert on_disk.count(b"\r") == 0     # LF everywhere, Windows included

    # 4. The public reader agrees with both.
    assert read(tmp_path) == [original]


def test_floats_and_none_survive_both_serialisations_exactly(tmp_path):
    write(FULL, tmp_path)
    doc = yaml.safe_load(yaml_path(tmp_path, FULL.record).read_text(encoding="utf-8"))
    blob = json.loads(_db_json(tmp_path, FULL.record))

    assert doc == blob                                    # same data, both stores
    assert doc["steps"][0]["measurements"][1]["value"] == -0.1234567890123
    assert blob["steps"][0]["measurements"][1]["value"] == -0.1234567890123
    assert doc["calls"][1]["result"] is None
    assert blob["calls"][1]["result"] is None

    # `started` must come back a str from YAML, not a datetime: an unquoted
    # ISO timestamp is a YAML `timestamp`, and JSON has no such type, so a
    # datetime field could never be identical across the two stores.
    assert doc["started"] == "2026-09-09T14:30:22Z"
    assert isinstance(doc["started"], str)


def test_key_order_is_the_specs_order_not_alphabetical(tmp_path):
    write(FULL, tmp_path)
    text = yaml_path(tmp_path, FULL.record).read_text(encoding="utf-8")
    # PyYAML does not indent block sequences, so `- name:` lines also start at
    # column 0; only top-level mapping keys are wanted here.
    keys = [m.group(1) for m in re.finditer(r"^([a-z_]+):", text, re.MULTILINE)]
    assert keys == [
        "record_version", "record", "unit", "station", "sequence", "sequence_version",
        "firmware", "setup", "setup_version", "runner", "started", "ended",
        "verdict", "steps", "calls",
    ]
    assert text.startswith("record_version:")   # §7: the first field


def test_optional_fields_are_omitted_when_unset(tmp_path):
    write(MINIMAL, tmp_path)
    doc = yaml.safe_load(yaml_path(tmp_path, MINIMAL.record).read_text(encoding="utf-8"))
    assert "firmware" not in doc and "abort" not in doc
    assert MINIMAL.firmware is None and MINIMAL.abort is None
    # §2 writes a one-sided limit as `limits: {max: 50}` — no `min` key.
    assert FULL.to_mapping()["steps"][1]["measurements"][0]["limits"] == {"max": 50}
    assert FULL.to_mapping()["steps"][0]["measurements"][1]["limits"] == {}


# --------------------------------------------------------------------------- #
# DoD: the YAML wins on disagreement (`record.md` §4)
# --------------------------------------------------------------------------- #

def test_yaml_wins_when_the_two_stores_disagree(tmp_path):
    write(FULL, tmp_path)

    # Rewrite only the audit copy: a different unit, and a verdict that follows
    # from a different set of steps. The db row keeps the original values in
    # both its JSON column and its indexed columns.
    truth = Record(**{**_as_kwargs(FULL), "unit": "SN-999999", "steps": ()})
    yaml_path(tmp_path, FULL.record).write_text(to_yaml(truth), encoding="utf-8",
                                                newline="\n")

    assert json.loads(_db_json(tmp_path, FULL.record))["unit"] == "SN-000417"
    assert _db_row(tmp_path, FULL.record)[1] == "SN-000417"     # the indexed column

    got = read(tmp_path)
    assert got == [truth]
    assert got[0].unit == "SN-999999"
    assert got[0].verdict == "pass"      # derived from the YAML's steps, not the db's

    # The filters are applied to the winning data, not to the db's stale index.
    assert read(tmp_path, unit="SN-999999") == [truth]
    assert read(tmp_path, unit="SN-000417") == []
    assert read(tmp_path, verdict="pass") == [truth]
    assert read(tmp_path, verdict="fail") == []


def test_a_yaml_only_record_is_still_returned(tmp_path):
    """The db is an index and can be rebuilt; the audit copy is the record."""
    write(FULL, tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("DELETE FROM records")
    assert read(tmp_path) == [FULL]


def test_a_db_only_record_is_not_silently_dropped(tmp_path):
    """No YAML copy is a problem, but losing the row entirely is a worse one."""
    write(FULL, tmp_path)
    yaml_path(tmp_path, FULL.record).unlink()
    assert read(tmp_path) == [FULL]


# --------------------------------------------------------------------------- #
# the invariants
# --------------------------------------------------------------------------- #

def test_verdict_is_derived_never_set_by_hand():
    step = Measurement(name="v", value=1.0, unit="V", limits=Limits(max=2), passed=True)
    ok = Step(name="s", verdict="pass", measurements=[step])
    bad = Step(name="s", verdict="fail", measurements=[step])
    boom = Step(name="s", verdict="error", measurements=[])

    assert _with(steps=[ok]).verdict == "pass"
    assert _with(steps=[ok, bad]).verdict == "fail"
    assert _with(steps=[ok, bad, boom]).verdict == "error"   # a raise outranks a fail
    aborted = _with(steps=[ok, bad],
                    abort=Abort(by="predictor", after_step="s",
                                reason="p_fail=0.91 after 3 of 9 steps"))
    assert aborted.verdict == "aborted"                      # abort outranks everything

    with pytest.raises(AttributeError):
        FULL.verdict = "pass"        # type: ignore[misc]  — derived, not a field


def test_a_stored_verdict_that_disagrees_with_its_steps_is_refused(tmp_path):
    write(FULL, tmp_path)
    doc = yaml.safe_load(yaml_path(tmp_path, FULL.record).read_text(encoding="utf-8"))
    doc["verdict"] = "pass"          # its steps say `fail`
    yaml_path(tmp_path, FULL.record).write_text(yaml.safe_dump(doc, sort_keys=False),
                                                encoding="utf-8", newline="\n")
    with pytest.raises(RecordError, match="derived"):
        read(tmp_path)


def test_abort_round_trips(tmp_path):
    rec = _with(record="rec-20260909T150000-abcdef",
                abort=Abort(by="human", after_step="measure_vout", reason="operator"))
    write(rec, tmp_path)
    assert _read_yaml(tmp_path, rec.record) == rec == _read_db(tmp_path, rec.record)
    assert read(tmp_path, verdict="aborted") == [rec]


# --------------------------------------------------------------------------- #
# #223: `record_version` is 2; a v2 reader reads v1; a newer record says so
# --------------------------------------------------------------------------- #

_NEWER = "this record is version 3, newer than this SHAL reads (up to 2) — upgrade pyshal"


def _write_doc(store, doc) -> None:
    path = yaml_path(store, doc["record"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")


def test_a_new_record_is_written_as_version_2(tmp_path):
    assert RECORD_VERSION == 2
    write(FULL, tmp_path)
    assert yaml_path(tmp_path, FULL.record).read_text(encoding="utf-8").startswith(
        "record_version: 2\n")
    assert json.loads(_db_json(tmp_path, FULL.record))["record_version"] == 2


def test_a_v1_record_still_reads_unchanged(tmp_path):
    doc = _with(calls=[]).to_mapping()
    doc["record_version"] = 1
    assert doc["calls"] == []                   # v1 always wrote `calls`
    _write_doc(tmp_path, doc)

    [got] = read(tmp_path)
    assert got == _with(calls=[], record_version=1)
    assert got.calls == ()                      # `calls: []` → "collected, none"
    assert got.to_mapping() == doc              # reads back exactly as written


@pytest.mark.parametrize("drop", [None, "unit", "steps", "verdict"])
def test_a_newer_record_is_refused_in_one_sentence_naming_no_key(drop):
    doc = FULL.to_mapping()
    doc["record_version"] = 3
    if drop:
        del doc[drop]                           # a v3 record may well lack a v2 key
    with pytest.raises(RecordError) as err:
        Record.from_mapping(doc, source="r.yaml")
    assert str(err.value) == f"r.yaml: {_NEWER}"
    assert "key" not in str(err.value)


def test_read_refuses_the_whole_store_when_one_record_is_newer(tmp_path):
    # #223 does not say whether read() skips a newer record. It refuses the
    # whole read, as before: a silent skip would under-report the station.
    write(MINIMAL, tmp_path)
    doc = FULL.to_mapping()
    doc["record_version"] = 3
    _write_doc(tmp_path, doc)
    with pytest.raises(RecordError, match=re.escape(_NEWER)):
        read(tmp_path)
    with pytest.raises(RecordError, match=re.escape(_NEWER)):
        read(tmp_path, newer="refuse")          # the default, spelled out


# --------------------------------------------------------------------------- #
# #227: read(store, newer="skip") — an explicit opt-in that says what it skipped
# --------------------------------------------------------------------------- #

def test_read_skip_returns_every_readable_record_and_the_skipped_list(tmp_path):
    write(MINIMAL, tmp_path)
    write(FULL, tmp_path)
    doc = _with(record="rec-20260909T160000-v3v3v3").to_mapping()
    doc["record_version"] = 3
    del doc["unit"]                             # a v3 record may well lack a v2 key
    _write_doc(tmp_path, doc)

    records, skipped = read(tmp_path, newer="skip")
    assert records == [MINIMAL, FULL]           # ordered by started, as ever
    assert skipped == [("rec-20260909T160000-v3v3v3", 3)]


def test_read_skip_takes_the_id_from_the_store_not_from_the_record(tmp_path):
    # A newer version may rename or drop the `record` field; the id a caller can
    # act on is the one the store files it under.
    doc = FULL.to_mapping()
    doc["record_version"] = 3
    _write_doc(tmp_path, doc)
    del doc["record"]
    yaml_path(tmp_path, FULL.record).write_text(yaml.safe_dump(doc, sort_keys=False),
                                                encoding="utf-8", newline="\n")
    assert read(tmp_path, newer="skip") == ([], [(FULL.record, 3)])


def test_read_skip_lists_a_newer_db_only_record_and_ignores_the_filters(tmp_path):
    write(FULL, tmp_path)
    doc = FULL.to_mapping()
    doc["record_version"] = 4
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("UPDATE records SET record_json = ?", (json.dumps(doc),))
    yaml_path(tmp_path, FULL.record).unlink()
    # A newer record cannot be filtered, so it is listed whatever the filter.
    assert read(tmp_path, unit="nobody", newer="skip") == ([], [(FULL.record, 4)])


def test_read_skip_on_a_store_with_nothing_newer_has_an_empty_skipped_list(tmp_path):
    write(FULL, tmp_path)
    assert read(tmp_path, newer="skip") == ([FULL], [])


def test_read_skip_still_raises_on_a_malformed_record(tmp_path):
    write(FULL, tmp_path)
    doc = MINIMAL.to_mapping()
    del doc["unit"]                             # malformed, not newer
    _write_doc(tmp_path, doc)
    with pytest.raises(RecordError, match="missing required key 'unit'"):
        read(tmp_path, newer="skip")


@pytest.mark.parametrize("bad", ["Skip", "ignore", "", None, True])
def test_read_refuses_any_other_value_of_newer(tmp_path, bad):
    write(FULL, tmp_path)
    with pytest.raises(ValueError, match="newer must be 'refuse' or 'skip'"):
        read(tmp_path, newer=bad)


@pytest.mark.parametrize("version", [0, -1])
def test_a_record_version_below_1_is_refused(version):
    doc = FULL.to_mapping()
    doc["record_version"] = version
    with pytest.raises(RecordError, match="record_version must be 1 or more"):
        Record.from_mapping(doc)


@pytest.mark.parametrize("drop", ["unit", "station", "sequence", "setup",
                                  "setup_version", "runner", "started", "ended",
                                  "steps"])
def test_every_field_but_firmware_abort_and_calls_is_required(drop):
    doc = FULL.to_mapping()
    del doc[drop]
    with pytest.raises(RecordError, match=f"missing required key '{drop}'"):
        Record.from_mapping(doc)


# --------------------------------------------------------------------------- #
# #218: `calls=None` means "not collected"; `()` means "collected, none"
# --------------------------------------------------------------------------- #

def test_calls_none_is_omitted_from_both_stores_and_reads_back_as_none(tmp_path):
    rec = _with(calls=None)
    write(rec, tmp_path)

    doc = yaml.safe_load(yaml_path(tmp_path, rec.record).read_text(encoding="utf-8"))
    assert "calls" not in doc
    assert "calls" not in json.loads(_db_json(tmp_path, rec.record))

    from_yaml = _read_yaml(tmp_path, rec.record)
    from_db = _read_db(tmp_path, rec.record)
    assert from_yaml.calls is None and from_db.calls is None
    assert from_yaml == rec == from_db
    assert to_yaml(from_db).encode() == to_yaml(rec).encode()
    assert read(tmp_path) == [rec]
    assert read(tmp_path)[0].calls is None


def test_calls_empty_still_means_collected_none_and_is_distinct_from_none(tmp_path):
    empty = _with(calls=())
    write(empty, tmp_path)

    doc = yaml.safe_load(yaml_path(tmp_path, empty.record).read_text(encoding="utf-8"))
    assert doc["calls"] == []
    assert json.loads(_db_json(tmp_path, empty.record))["calls"] == []
    assert _read_yaml(tmp_path, empty.record).calls == ()
    assert _read_db(tmp_path, empty.record).calls == ()
    assert read(tmp_path)[0].calls == ()
    assert empty != _with(calls=None)


def test_an_old_record_with_calls_empty_list_reads_back_as_empty_tuple(tmp_path):
    """No migration: a record written before #218 carries `calls: []`."""
    doc = MINIMAL.to_mapping()
    doc["calls"] = []
    path = yaml_path(tmp_path, MINIMAL.record)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8", newline="\n")
    conn = sqlite3.connect(db_path(tmp_path))  # an old db row with no YAML copy
    with conn:
        conn.execute("CREATE TABLE records (record TEXT PRIMARY KEY, unit TEXT, "
                     "station TEXT, sequence TEXT, verdict TEXT, started TEXT, "
                     "record_json TEXT)")
        old = {**doc, "record": "rec-old-db-only"}
        conn.execute("INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (old["record"], old["unit"], old["station"], old["sequence"],
                      old["verdict"], old["started"], json.dumps(old)))
    conn.close()

    got = read(tmp_path)
    assert [r.calls for r in got] == [(), ()]


@pytest.mark.parametrize("bad", ["psu.set_voltage", {"capability": "x"}, 5, True])
def test_calls_of_a_bad_type_is_still_refused(bad):
    doc = FULL.to_mapping()
    doc["calls"] = bad
    with pytest.raises(RecordError, match="'calls' must be a list"):
        Record.from_mapping(doc)


def test_a_call_payload_that_cannot_round_trip_is_refused(tmp_path):
    import datetime
    rec = _with(calls=[{"capability": "x", "result": datetime.date(2026, 9, 9)}])
    with pytest.raises(RecordError, match="date"):
        write(rec, tmp_path)
    with pytest.raises(RecordError, match="finite"):
        write(_with(calls=[{"capability": "x", "result": float("nan")}]), tmp_path)


def test_a_record_id_cannot_name_another_file(tmp_path):
    with pytest.raises(RecordError, match="safe file name"):
        write(_with(record="../../etc/passwd"), tmp_path)


def test_error_messages_never_echo_a_value():
    """A record can carry whatever a driver returned; a value may be a secret."""
    doc = FULL.to_mapping()
    doc["calls"][0]["result"] = {"token": "hunter2-super-secret"}
    doc["calls"][0]["extra"] = object()
    with pytest.raises(RecordError) as exc:
        Record.from_mapping(doc)
    assert "hunter2" not in str(exc.value)
    assert "calls[0].extra" in str(exc.value)


def test_the_db_indexes_the_columns_the_floor_screen_queries(tmp_path):
    write(FULL, tmp_path)
    with sqlite3.connect(db_path(tmp_path)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(records)")]
        idx = {r[1] for r in conn.execute("PRAGMA index_list(records)")}
    assert cols == ["record", "unit", "station", "sequence", "verdict",
                    "started", "record_json"]
    assert {"records_unit", "records_station", "records_sequence",
            "records_verdict", "records_started"} <= idx


def test_writing_the_same_id_twice_replaces_both_copies(tmp_path):
    write(FULL, tmp_path)
    again = Record(**{**_as_kwargs(FULL), "unit": "SN-000418"})
    write(again, tmp_path)
    assert read(tmp_path) == [again]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT count(*) FROM records").fetchone()[0] == 1


def test_read_filters_and_orders_by_started(tmp_path):
    a = _with(record="rec-a", station="line2-st4", started="2026-09-09T01:00:00Z")
    b = _with(record="rec-b", station="line2-st5", started="2026-09-09T00:00:00Z")
    write(a, tmp_path)
    write(b, tmp_path)
    assert [r.record for r in read(tmp_path)] == ["rec-b", "rec-a"]
    assert read(tmp_path, station="line2-st5") == [b]
    assert read(tmp_path, sequence="psu-bringup") == [b, a]
    assert read(tmp_path, unit="nobody") == []


def test_reading_an_empty_store_is_empty_not_an_error(tmp_path):
    assert read(tmp_path) == []


# --------------------------------------------------------------------------- #
# runner: a closed set of three — pytest | bricks | script (#214, D22)
# --------------------------------------------------------------------------- #

def test_a_script_record_is_written_read_back_and_filtered(tmp_path):
    """`script` is operator code that is neither runner; `read()` takes it like the others."""
    mine = _with(record="rec-script", runner="script", station="bench-hemi",
                 started="2026-09-09T01:00:00Z")
    other = _with(record="rec-pytest", station="line2-st4",
                  started="2026-09-09T00:00:00Z")
    write(mine, tmp_path)
    write(other, tmp_path)
    assert _read_yaml(tmp_path, "rec-script") == mine == _read_db(tmp_path, "rec-script")
    assert read(tmp_path) == [other, mine]
    assert read(tmp_path, station="bench-hemi") == [mine]
    assert read(tmp_path, station="bench-hemi", verdict="fail") == [mine]
    assert read(tmp_path, sequence="psu-bringup") == [other, mine]


def test_a_fourth_runner_is_refused_and_the_message_names_all_three():
    doc = FULL.to_mapping()
    doc["runner"] = "custom"
    with pytest.raises(RecordError, match="'runner' must be one of") as exc:
        Record.from_mapping(doc)
    assert "bricks, pytest, script" in str(exc.value)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _as_kwargs(rec: Record) -> dict:
    return {f: getattr(rec, f) for f in rec.__dataclass_fields__}


def _with(**changes) -> Record:
    return Record(**{**_as_kwargs(FULL), **changes})


def _db_row(store, record_id):
    with sqlite3.connect(db_path(store)) as conn:
        return conn.execute("SELECT * FROM records WHERE record = ?",
                            (record_id,)).fetchone()


def _db_json(store, record_id) -> str:
    with sqlite3.connect(db_path(store)) as conn:
        return conn.execute("SELECT record_json FROM records WHERE record = ?",
                            (record_id,)).fetchone()[0]
