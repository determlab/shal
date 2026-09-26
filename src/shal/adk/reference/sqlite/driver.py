"""sqlite,database — a database as a root driver: four side-effect labels on one node.

The shape for software that has a client library (ADK §3.7, S2): `kind = None`,
no SHAL bus, wrap the library, connect lazily. The library is the stdlib
`sqlite3`, so there are zero dependencies. The node address is what
`sqlite3.connect()` gets: `":memory:"` is the twin (a private in-memory
database, nothing written to disk), a file path is the real thing.

The labels follow the software rule (S3, AGENT_GUIDE.md "Side effects for
software"):

- `query`       -> `none`     a SELECT. Live or raise.
- `insert`      -> `write`    undone by this driver's own `delete_row`.
- `delete_row`  -> `write`    undone by this driver's own `insert`: it returns
                              the removed row, rowid included, and `insert` of
                              that row puts it back exactly. Not "DELETE for
                              good", so not an actuator.
- `execute_ddl` -> `config`   CREATE / ALTER ... ADD|RENAME: changes what the
                              database accepts next. Gated.
- `drop_table`  -> `actuator` DROP: cannot be undone by any op here. Gated.

A `write` runs without asking, so `insert` and `delete_row` PROVE, inside their
own transaction, that one op can undo them: the target is a real rowid table
(not a view, not WITHOUT ROWID), exactly one row changed
(`total_changes` went up by exactly 1), and every value in it is one `insert`
accepts. Anything else rolls back and is refused with the reason: a trigger that
writes another table, an `ON DELETE CASCADE`, a `REPLACE` conflict that removed
another row, or a row `insert` could not put back (a BLOB). The label is static
metadata, so it cannot depend on the address; the proof runs on every call, on
`":memory:"` and on a real file alike.

SQL injection: a VALUE is always a `?` parameter, never text in the SQL. A table
or column NAME cannot be a parameter, so it must match `_IDENT` and is then
double-quoted. Both checks run before the connection is touched. `execute_ddl`
takes SQL text on purpose (DDL has no parameter form); it is gated, one
statement only, and never a DROP — a DROP goes through `drop_table`, so its
label stays honest.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from typing import Any

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.errors import HopError
from shal.log import current_txn

# a table or column name: plain identifier, nothing to escape. sqlite_* is SQLite's own.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCALARS = (str, int, float, type(None))  # what a `?` parameter may carry here
# sqlite3 refuses a second statement with sqlite3.Warning on Python 3.10 (not an
# sqlite3.Error); 3.11+ raises ProgrammingError. Either way nothing ran.
_SQL_ERRORS = (sqlite3.Error, sqlite3.Warning)


def _name(ident: str) -> str:
    """A validated, double-quoted identifier — the only way a name enters SQL."""
    if not isinstance(ident, str) or not _IDENT.fullmatch(ident) \
            or ident.lower().startswith("sqlite_"):
        raise ValueError(f"not a plain table/column name: {ident!r} "
                         f"(letters, digits, _; not sqlite_*)")
    return f'"{ident}"'


def _row_arg(text: str, what: str) -> dict[str, Any]:
    """Parse a JSON object of column -> scalar value (an op param is a string)."""
    try:
        row = json.loads(text)
    except ValueError as e:
        raise ValueError(f"{what} must be a JSON object, got {text!r}") from e
    if not isinstance(row, dict):
        raise ValueError(f"{what} must be a JSON object, got {text!r}")
    for col, value in row.items():
        _name(col)
        if isinstance(value, bool) or not isinstance(value, _SCALARS):
            raise ValueError(f"{what}[{col!r}] must be text, a number or null")
    return row


def _rowid_table(conn: sqlite3.Connection, table: str, refused: str) -> None:
    """Refuse unless `table` is a real table with a rowid: the undo is by rowid,
    so a view (its INSTEAD OF trigger writes elsewhere) or a WITHOUT ROWID table
    has no undo here. A TEMP object wins name resolution, so temp is read first,
    and the name must be a plain rowid table in EVERY schema that has it."""
    kinds = [(schema, *kind) for schema in ("temp", "main")
             for kind in conn.execute(
                 f"SELECT type FROM {schema}.sqlite_master "
                 f"WHERE name = ? COLLATE NOCASE", (table,)).fetchall()]
    if not kinds:
        raise sqlite3.OperationalError(f"no such table: {table}")
    for schema, kind in kinds:
        if kind != "table":
            raise ValueError(f"{refused} — it is a {kind} ({schema}), not a table, "
                             f"so this driver cannot undo a change to it")
        try:
            conn.execute(f"SELECT rowid FROM {schema}.{_name(table)} LIMIT 0")
        except sqlite3.OperationalError as e:
            raise ValueError(f"{refused} — it has no rowid (WITHOUT ROWID?), so "
                             f"this driver cannot undo a change to it") from e


def _one_row_changed(conn: sqlite3.Connection, before: int, refused: str) -> None:
    """Raise (so the caller's transaction rolls back) unless exactly one row
    changed since `before`. `total_changes` counts trigger and cascade writes."""
    delta = conn.total_changes - before
    if delta != 1:
        raise ValueError(f"{refused} — {delta} rows changed, not exactly 1 (a trigger "
                         f"or a cascade touched other rows), so one op could not undo "
                         f"it; rolled back")


@registry.register
class SqliteDatabase(Driver):
    compatible = "sqlite,database"
    kind = None          # root driver: wraps sqlite3 directly, no SHAL bus
    llm_ready = True

    def bind(self, node) -> None:
        super().bind(node)
        self._path = str(node.address)  # ":memory:" = the twin; else a file path
        self._conn: sqlite3.Connection | None = None  # lazy: opened on the first op
        self._lock = threading.Lock()   # one connection, one caller at a time

    # -- client (lazy) ---------------------------------------------------------
    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                # autocommit mode: every transaction below is an explicit BEGIN/COMMIT
                self._conn = sqlite3.connect(self._path, isolation_level=None,
                                             check_same_thread=False)
            except _SQL_ERRORS as e:
                raise self._hop(e, "no") from e
        return self._conn

    def _hop(self, e: Exception, delivered: str) -> HopError:
        return HopError(f"sqlite {self._path}: {e}", path=self.node.path,
                        hop="sqlite", txn=current_txn.get(), delivered=delivered)

    def _read(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        """A SELECT, answered by the database now (live or raise)."""
        with self._lock:
            try:
                cur = self._db().execute(sql, args)
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
            except _SQL_ERRORS as e:
                raise self._hop(e, "no") from e

    def _change(self, work):
        """Run `work(conn)` in one transaction. An error before COMMIT rolls it
        all back, so nothing changed (delivered="no"). An error AT commit may or
        may not have landed (delivered="unknown") — the user decides, no retry."""
        with self._lock:
            conn = self._db()
            try:
                conn.execute("BEGIN IMMEDIATE")
                result = work(conn)
            except BaseException as e:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                if isinstance(e, _SQL_ERRORS):
                    raise self._hop(e, "no") from e
                raise
            try:
                conn.execute("COMMIT")
            except _SQL_ERRORS as e:
                raise self._hop(e, "unknown") from e
            return result

    # -- none: a read -----------------------------------------------------------
    @idempotent  # a read: safe to retry
    @op("Read rows from a table now. `where` is a JSON object of column -> value "
        "(all must be equal), e.g. '{\"name\": \"a\"}'; '{}' reads every row. "
        "Each row includes its `rowid`.", side_effect="none",
        params={"limit": {"minimum": 1, "maximum": 1000}})
    def query(self, table: str, where: str = "{}", limit: int = 100) -> list:
        cond = _row_arg(where, "where")
        sql = f'SELECT rowid AS "rowid", * FROM {_name(table)}'
        if cond:
            sql += " WHERE " + " AND ".join(f"{_name(c)} IS ?" for c in cond)
        sql += " ORDER BY rowid LIMIT ?"
        return self._read(sql, (*cond.values(), limit))

    # -- write: undone by this driver's own op ------------------------------------
    @op("Insert one row and return its rowid. `row` is a JSON object of column -> "
        "value, e.g. '{\"name\": \"a\", \"qty\": 2}'. Undo it with delete_row.",
        side_effect="write")
    def insert(self, table: str, row: str) -> int:
        values = _row_arg(row, "row")
        cols = ", ".join(_name(c) for c in values)
        marks = ", ".join("?" for _ in values)
        sql = (f"INSERT INTO {_name(table)} ({cols}) VALUES ({marks})" if values
               else f"INSERT INTO {_name(table)} DEFAULT VALUES")
        count = f"SELECT count(*) FROM {_name(table)}"

        def work(conn):
            _rowid_table(conn, table, f"{table}: not inserted")
            before, rows = conn.total_changes, conn.execute(count).fetchone()[0]
            rowid = conn.execute(sql, tuple(values.values())).lastrowid
            _one_row_changed(conn, before, f"{table}: not inserted")
            if conn.execute(count).fetchone()[0] != rows + 1:  # REPLACE hides a delete
                raise ValueError(f"{table}: not inserted — a REPLACE conflict removed "
                                 f"another row, so delete_row could not undo it")
            # the rowid we return must name the row we wrote, or delete_row misses
            if conn.execute(f"SELECT 1 FROM {_name(table)} WHERE rowid = ?",
                            (rowid,)).fetchone() is None:
                raise ValueError(f"{table}: not inserted — rowid {rowid} does not name "
                                 f"the new row, so delete_row could not undo it")
            return rowid
        return self._change(work)

    @op("Delete one row by rowid and return it (rowid included). Undo it by "
        "passing that row, as JSON, to insert.", side_effect="write",
        params={"rowid": {"minimum": 1, "maximum": 2**63 - 1}})
    def delete_row(self, table: str, rowid: int) -> dict:
        name = _name(table)

        def work(conn):
            _rowid_table(conn, table, f"{table} rowid {rowid}: not deleted")
            cur = conn.execute(f'SELECT rowid AS "rowid", * FROM {name} WHERE rowid = ?',
                               (rowid,))
            found = cur.fetchone()
            if found is None:
                raise LookupError(f"{table}: no row with rowid {rowid}")
            removed = dict(zip([d[0] for d in cur.description], found, strict=True))
            # `write` promises insert can put this row back. Check it BEFORE the
            # DELETE: a BLOB, or a column name insert would refuse, means no undo,
            # so raise (the transaction rolls back) and delete nothing.
            try:
                _row_arg(json.dumps(removed), "row")
            except (TypeError, ValueError) as e:
                raise ValueError(
                    f"{table} rowid {rowid}: not deleted — insert could not put this "
                    f"row back ({e}), and delete_row is a `write`, so it only deletes "
                    f"what it can undo") from e
            before = conn.total_changes
            conn.execute(f"DELETE FROM {name} WHERE rowid = ?", (rowid,))
            _one_row_changed(conn, before, f"{table} rowid {rowid}: not deleted")
            return removed
        return self._change(work)

    # -- config: changes what the database does next (gated) ----------------------
    @op("Change the schema with ONE statement: CREATE (table, index, view, "
        "trigger) or ALTER TABLE ... ADD / RENAME. Never DROP — use drop_table.",
        side_effect="config")
    def execute_ddl(self, sql: str) -> None:
        words = sql.upper().split()
        allowed = (words[:1] == ["CREATE"]
                   or (words[:2] == ["ALTER", "TABLE"] and len(words) > 3
                       and words[3] in ("ADD", "RENAME")))
        if not allowed:
            raise ValueError("execute_ddl takes one CREATE or ALTER TABLE ... "
                             f"ADD/RENAME statement, got {sql!r}")
        # sqlite3 runs one statement per execute(): "CREATE ...; DROP ..." is
        # refused before anything runs
        self._change(lambda c: c.execute(sql))

    # -- actuator: cannot be undone (gated) ----------------------------------------
    @op("Drop a table and every row in it, for good. Nothing here can undo it.",
        side_effect="actuator")
    def drop_table(self, table: str) -> None:
        sql = f"DROP TABLE {_name(table)}"
        self._change(lambda c: c.execute(sql))

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "sqlite3.connect() target: ':memory:' "
                                              "(the twin) or a database file path",
                               "examples": [":memory:", "data/app.db"]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
