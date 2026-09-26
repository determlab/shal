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
