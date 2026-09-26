"""Tests for the sqlite,database reference driver, on address ":memory:".

The address is the twin: every test gets its own in-memory database, so there
is no sim.py and no file on disk. `write` is proven by undoing it with the
driver's own op; `config` and `actuator` are proven by the gate stopping them.

Run from anywhere: this file puts its own folder on sys.path, so `driver` is the
file next to it. Copy the files, rename, and keep going.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402  (registers sqlite,database)

import shal  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


def _schema(db) -> list:
    """Every table/index/view/trigger in the database, read past the driver."""
    return db._db().execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY name").fetchall()


@pytest.fixture
def db():
    """A fresh in-memory database with one table and one row in it."""
    with shal.load(_TOPO) as hal:
        dev = hal.get_device("db")
        with shal.approver(shal.AutoApprove()):  # the setup DDL is gated too
            dev.execute_ddl("CREATE TABLE items (name TEXT, qty INTEGER)")
        dev.insert("items", json.dumps({"name": "bolt", "qty": 5}))
        yield dev


def test_the_four_labels_and_the_undo_op():
    labels = {name: fn.__shal_op__["side_effect"]
              for name, fn in driver.SqliteDatabase.capability_ops().items()}
    assert labels == {"query": "none", "insert": "write", "delete_row": "write",
                      "execute_ddl": "config", "drop_table": "actuator"}


def test_query_reads_live_rows_with_their_rowid(db):
    assert db.query("items") == [{"rowid": 1, "name": "bolt", "qty": 5}]
    assert db.query("items", where='{"name": "nut"}') == []


def test_insert_is_undone_by_delete_row(db):
    start, schema = db.query("items"), _schema(db)
    rowid = db.insert("items", json.dumps({"name": "nut", "qty": 7}))
    assert db.query("items", where='{"name": "nut"}')[0]["rowid"] == rowid
    db.delete_row("items", rowid)
    assert db.query("items") == start and _schema(db) == schema


def test_delete_row_is_undone_by_insert(db):
    start = db.query("items")
    removed = db.delete_row("items", 1)
    assert removed == {"rowid": 1, "name": "bolt", "qty": 5}
    assert db.query("items") == []
    db.insert("items", json.dumps(removed))  # the returned row puts it back exactly
    assert db.query("items") == start


def test_a_plain_row_is_exactly_one_change_each_way(db):
    conn = db._db()
    db.insert("items", '{"name": "nut", "qty": 7}')  # rowid 2: not the first row
    start = db.query("items")
    before = conn.total_changes
    removed = db.delete_row("items", 2)
    assert conn.total_changes - before == 1
    before = conn.total_changes
    assert db.insert("items", json.dumps(removed)) == 2  # same rowid back
    assert conn.total_changes - before == 1
    assert db.query("items") == start


def _tables(db, *names) -> list:
    """Every row of each table, read past the driver (rowid included)."""
    return [db._db().execute(f"SELECT rowid, * FROM {n} ORDER BY rowid").fetchall()
            for n in names]


@pytest.mark.parametrize("event, call", [
    ("INSERT", lambda d: d.insert("items", '{"name": "nut", "qty": 7}')),
    ("DELETE", lambda d: d.delete_row("items", 1)),
])
def test_a_trigger_writing_another_table_is_refused(db, event, call):
    with shal.approver(shal.AutoApprove()):  # a trigger comes only through config
        db.execute_ddl("CREATE TABLE log (msg TEXT)")
        db.execute_ddl(f"CREATE TRIGGER t AFTER {event} ON items "
                       f"BEGIN INSERT INTO log VALUES ('{event}'); END")
    start = _tables(db, "items", "log")
    with pytest.raises(ValueError, match="2 rows changed, not exactly 1"):
        call(db)
    assert _tables(db, "items", "log") == start  # nothing changed in either table


@pytest.fixture
def family(db):
    """A parent row with one child that references it ON DELETE CASCADE."""
    with shal.approver(shal.AutoApprove()):
        db.execute_ddl("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)")
        db.execute_ddl("CREATE TABLE child (pid INTEGER "
                       "REFERENCES parent(id) ON DELETE CASCADE)")
    db.insert("parent", '{"id": 1, "name": "p"}')
    db.insert("child", '{"pid": 1}')
    return db


def test_the_driver_never_turns_foreign_keys_on_so_no_cascade_fires(family):
    # SQLite's default is foreign_keys OFF per connection, the driver never sets
    # it, and execute_ddl refuses a PRAGMA. So a cascade cannot fire through the
    # driver: deleting the parent is one change, and insert undoes it exactly.
    db = family
    assert db._db().execute("PRAGMA foreign_keys").fetchone() == (0,)
    with shal.approver(shal.AutoApprove()), pytest.raises(ValueError):
        db.execute_ddl("PRAGMA foreign_keys = ON")
    start = _tables(db, "parent", "child")
    removed = db.delete_row("parent", 1)
    assert _tables(db, "child") == [start[1]]  # the child was not touched
    db.insert("parent", json.dumps(removed))
    assert _tables(db, "parent", "child") == start


def test_a_cascade_is_refused_when_foreign_keys_are_on(family):
    # a copy that turns foreign keys on (set here past the driver, on its own
    # connection): the cascade is counted, and delete_row refuses it
    db = family
    db._db().execute("PRAGMA foreign_keys = ON")
    start = _tables(db, "parent", "child")
    with pytest.raises(ValueError, match="2 rows changed, not exactly 1"):
        db.delete_row("parent", 1)
    assert _tables(db, "parent", "child") == start


def test_a_replace_conflict_that_removes_a_row_is_refused(db):
    # REPLACE deletes the old row without counting it in total_changes;
    # the row count catches it
    with shal.approver(shal.AutoApprove()):
        db.execute_ddl("CREATE TABLE u (k TEXT UNIQUE ON CONFLICT REPLACE, v TEXT)")
    db.insert("u", '{"k": "a", "v": "old"}')
    start = _tables(db, "u")
    with pytest.raises(ValueError, match="REPLACE conflict"):
        db.insert("u", '{"k": "a", "v": "new"}')
    assert _tables(db, "u") == start


def test_delete_row_refuses_a_row_insert_cannot_put_back(db):
    # a BLOB is not a value insert takes, so deleting it could not be undone:
    # the `write` op refuses and the row stays
    with shal.approver(shal.AutoApprove()):
        db.execute_ddl("CREATE TABLE b (name TEXT, data BLOB)")
    db._db().execute("INSERT INTO b VALUES ('x', x'00ff')")  # written past the driver
    start = db._db().execute("SELECT rowid, * FROM b").fetchall()
    with pytest.raises(ValueError, match="not deleted"):
        db.delete_row("b", 1)
    assert db._db().execute("SELECT rowid, * FROM b").fetchall() == start
    assert start == [(1, "x", b"\x00\xff")]


@pytest.mark.parametrize("call", [
    lambda d: d.execute_ddl("CREATE TABLE audit (msg TEXT)"),
    lambda d: d.execute_ddl("ALTER TABLE items ADD COLUMN price REAL"),
    lambda d: d.drop_table("items"),
])
def test_config_and_actuator_stop_at_the_gate(db, call):
    start, schema = db.query("items"), _schema(db)
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            call(db)
    assert db.query("items") == start and _schema(db) == schema  # nothing ran


def test_approved_drop_table_is_gone(db):
    with shal.approver(shal.AutoApprove()):
        db.drop_table("items")
    with pytest.raises(shal.HopError):  # a read of a missing table raises, never []
        db.query("items")


@pytest.mark.parametrize("call", [
    lambda d: d.query('items" WHERE 1=1; --'),
    lambda d: d.query("items", where='{"name = name OR 1": 1}'),
    lambda d: d.insert("items; DROP TABLE items", '{"name": "x"}'),
    lambda d: d.delete_row("sqlite_master", 1),
    lambda d: d.drop_table("items; DROP TABLE other"),
    lambda d: d.execute_ddl("DROP TABLE items"),          # a DROP is drop_table's
    lambda d: d.execute_ddl("ALTER TABLE items DROP COLUMN qty"),
])
def test_names_and_ddl_are_checked_before_any_sql(db, call):
    start, schema = db.query("items"), _schema(db)
    with shal.approver(shal.AutoApprove()):
        with pytest.raises(ValueError):
            call(db)
    assert db.query("items") == start and _schema(db) == schema


def test_a_second_statement_in_ddl_is_refused_before_it_runs(db):
    start, schema = db.query("items"), _schema(db)
    with shal.approver(shal.AutoApprove()):
        with pytest.raises(shal.HopError) as ei:
            db.execute_ddl("CREATE TABLE x (a); DROP TABLE items")
    assert ei.value.delivered == "no"
    assert db.query("items") == start and _schema(db) == schema


def test_a_value_is_a_parameter_never_sql(db):
    text = "x'); DROP TABLE items; --"
    rowid = db.insert("items", json.dumps({"name": text}))
    assert db.query("items", where=json.dumps({"name": text}))[0]["rowid"] == rowid
    assert len(db.query("items")) == 2  # the table is still there


def test_memory_address_writes_no_file(monkeypatch):
    base = tempfile.TemporaryDirectory()  # not tmp_path: runs wherever it is copied
    cwd, tmp = Path(base.name, "cwd"), Path(base.name, "tmp")
    cwd.mkdir()
    tmp.mkdir()
    monkeypatch.chdir(cwd)
    for var in ("TMP", "TEMP", "TMPDIR", "SQLITE_TMPDIR"):
        monkeypatch.setenv(var, str(tmp))
    with shal.load(_TOPO) as hal:
        dev = hal.get_device("db")
        with shal.approver(shal.AutoApprove()):
            dev.execute_ddl("CREATE TABLE t (v TEXT)")
            rowid = dev.insert("t", '{"v": "a"}')
            dev.query("t")
            dev.delete_row("t", rowid)
            dev.drop_table("t")
    monkeypatch.undo()  # step out of cwd before the folder goes
    try:
        assert list(cwd.iterdir()) == [] and list(tmp.iterdir()) == []
    finally:
        base.cleanup()


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.SqliteDatabase, _TOPO)
    assert report.problems == []
    assert report.warnings == []
