"""The record — one result per unit, shared by bench and station (D22, `record.md`).

One record per **run of one sequence against one unit**, in one shape whether
pytest ran it at the bench or Bricks ran it on the floor. This module is the only
home for that shape, its writer and its reader; the runners import it and none of
them invents a shape of its own (`record.md` §3, §6 R1).

Invariants this file enforces — they are the contract, and the tests check them:

1.  **Every field of `record.md` §2 is required except `firmware`, `abort` and
    `calls`.** A bench run is `unit="bench"`, never blank.
2.  **`verdict` is derived, never set by hand** — `aborted` if the run was stopped
    early, else `error` if a step raised, else `fail` if a step failed, else
    `pass`. It is a read-only property, so it cannot be constructed wrong; a
    stored record whose `verdict` disagrees with its own steps is a
    `RecordError`, not a silent correction.
3.  **Measurements carry the limits that were enforced**, copied in at the time.
    A record must be readable in a year without the topology.
4.  **`calls` is the AOS list as it exists, unchanged** — pass-through mappings,
    not a shape this module defines. It is checked only for being plain,
    finite, JSON/YAML-safe data, because that is what the round-trip needs.
    **`calls=None` means "not collected"** — the key is omitted from the YAML
    and from the db's JSON, and reads back as `None`. `calls=()` means
    "collected, and there were none" and is written `calls: []` (#218).
5.  **Two stores, one truth: the YAML wins.** SQLite `records.db` is the index;
    `records/<id>.yaml` beside it is the audit copy. `read()` returns what the
    YAML says whenever a YAML file exists (`record.md` §4).
6.  **Fields are added, never renamed or removed**, and `record_version` is the
    first field so a reader knows what it holds (`record.md` §7). A change an
    older reader cannot read (a required key made optional, as #218 did to
    `calls`) bumps it; this reader reads every version from 1 up to its own.
    A record written by a *newer* version is refused, not half-read, in one
    sentence that names both versions and never a key (#223). `read(store)`
    refuses the whole read for one newer record; `read(store, newer="skip")` is
    the explicit opt-in that returns the readable records **and** the list of
    `(id, record_version)` it skipped — loud, never silent, never the default
    (#227).

Determinism: `started`/`ended` are ISO-8601 UTC **strings**, not `datetime` —
PyYAML would parse a bare timestamp back into a `datetime` while JSON would hand
back a string, so a `datetime` field could not round-trip identically through
both stores. Optional fields are omitted when unset (as `record.md` §2 itself
omits `min` from `limits: {max: 50}`), key order is the spec's order, and
non-finite floats are refused — together that makes the two serialisations of
one record byte-stable.

No consumer wiring lives here: `pytest-shal` (R2), Bricks/AOS (R3) and Predictor
(R4) import this module later. Nothing in `shal` imports it today.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, overload

import yaml

from .errors import Error

# `record.md` §7 — the first field, so a reader knows what it holds. 2 since
# #218 made `calls` optional, which a version-1 reader cannot read (#223).
RECORD_VERSION = 2

#: File layout under a store directory (`record.md` §4).
DB_NAME = "records.db"
YAML_DIRNAME = "records"

Verdict = Literal["pass", "fail", "error", "aborted"]
StepVerdict = Literal["pass", "fail", "error"]
# `script` is operator code that is neither runner — a loop a person wrote, a
# one-off bench script. It records provenance; no plugin produces it. A closed
# set, not a free string: a reader must be able to enumerate who wrote a record
# (`record.md` §2, #214).
Runner = Literal["pytest", "bricks", "script"]
AbortBy = Literal["predictor", "human"]

_VERDICTS = frozenset(("pass", "fail", "error", "aborted"))
_STEP_VERDICTS = frozenset(("pass", "fail", "error"))
_RUNNERS = frozenset(("pytest", "bricks", "script"))
_ABORT_BY = frozenset(("predictor", "human"))

# A record id becomes a file name (`records/<id>.yaml`), so it may not be able to
# name anything but itself. Matches the spec's `rec-20260909T143022-8f1a2c`.
_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

StoreLike = str | os.PathLike[str]


class RecordError(Error):
    """A record is malformed, inconsistent, or written by a newer `record_version`.

    Deliberately its own type under `Error` rather than `LoadError` or `HopError`
    (D18): nothing hopped, and this is not the topology load path — `LoadError` is
    defined as "anything wrong *before* runtime" for a *setup*, and a result read
    back months later is neither. Messages name the file and the offending key,
    never the offending value: a record carries whatever a driver returned in
    `calls[].result`, so a value could be a credential and must not be echoed.
    """


class _NewerRecordError(RecordError):
    """A record written by a newer `record_version` — the one `RecordError` that
    `read(store, newer="skip")` may skip (#227). Private: a caller catches
    `RecordError`, and the message is the same one sentence either way.
    """

    def __init__(self, version: int, message: str) -> None:
        super().__init__(message)
        self.version = version

    def __reduce__(self) -> tuple[Any, ...]:  # picklable despite the extra argument
        return (type(self), (self.version, str(self)))


# --------------------------------------------------------------------------- #
# the shape (`record.md` §2)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True, kw_only=True)
class Limits:
    """The limits *that were enforced*, copied from the SHAL op schema at the time.

    Both bounds are optional and each is omitted from the serialised form when
    unset — `record.md` §2 writes a one-sided limit as `limits: {max: 50}`.
    """

    min: float | None = None
    max: float | None = None


@dataclass(frozen=True, kw_only=True)
class Measurement:
    """One measured value with the limits it was judged against.

    `passed` is serialised under the spec's key **`pass`** — `pass` is a Python
    keyword and cannot be an attribute name. The wire key is the spec's; only the
    Python attribute differs.
    """

    name: str
    value: float
    unit: str
    limits: Limits
    passed: bool


@dataclass(frozen=True, kw_only=True)
class Step:
    """One step of the sequence and every measurement it took."""

    name: str
    verdict: StepVerdict
    measurements: tuple[Measurement, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "measurements", tuple(self.measurements))


@dataclass(frozen=True, kw_only=True)
class Abort:
    """Present only when the run was stopped early — by Predictor or by a person."""

    by: AbortBy
    after_step: str
    reason: str


@dataclass(frozen=True, kw_only=True)
class Record:
    """One run of one sequence against one unit (`record.md` §2).

    Keyword-only by construction: fifteen fields are far too many to order by
    position, and §7's "fields are added, never renamed or removed" means new
    fields must be able to appear anywhere without moving the existing ones.
    """

    record: str
    unit: str
    station: str
    sequence: str
    sequence_version: str
    setup: str
    setup_version: str
    runner: Runner
    started: str
    ended: str
    steps: tuple[Step, ...] = ()
    calls: tuple[dict[str, Any], ...] | None = ()
    firmware: str | None = None
    abort: Abort | None = None
    record_version: int = RECORD_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "steps", tuple(self.steps))
        if self.calls is not None:
            object.__setattr__(self, "calls", tuple(dict(c) for c in self.calls))

    @property
    def verdict(self) -> Verdict:
        """Derived, never set by hand (`record.md` §2).

        `aborted` dominates: an `abort` block exists only when the run was
        stopped early, and its steps are by definition incomplete. `error` then
        outranks `fail` — a step that raised leaves the run's own validity in
        doubt, which is a stronger statement than a value out of limits.
        """
        if self.abort is not None:
            return "aborted"
        if any(s.verdict == "error" for s in self.steps):
            return "error"
        if any(s.verdict == "fail" for s in self.steps):
            return "fail"
        return "pass"

    # ---- serialisation ---------------------------------------------------- #

    def to_mapping(self) -> dict[str, Any]:
        """Plain data in `record.md` §2's key order — the one form both stores hold."""
        out: dict[str, Any] = {
            "record_version": self.record_version,
            "record": self.record,
            "unit": self.unit,
            "station": self.station,
            "sequence": self.sequence,
            "sequence_version": self.sequence_version,
        }
        if self.firmware is not None:
            out["firmware"] = self.firmware
        out["setup"] = self.setup
        out["setup_version"] = self.setup_version
        out["runner"] = self.runner
        out["started"] = self.started
        out["ended"] = self.ended
        out["verdict"] = self.verdict
        out["steps"] = [_step_to_mapping(s) for s in self.steps]
        if self.calls is not None:  # None = not collected: omitted, not `[]`
            out["calls"] = [dict(c) for c in self.calls]
        if self.abort is not None:
            out["abort"] = {
                "by": self.abort.by,
                "after_step": self.abort.after_step,
                "reason": self.abort.reason,
            }
        return out

    @classmethod
    def from_mapping(cls, data: Any, *, source: str = "<mapping>") -> Record:
        """Rebuild a record from plain data, validating every invariant above."""
        m = _as_mapping(data, "record", source)
        version = _req(m, "record_version", int, source)
        # Checked before any other key: a newer record may lack a key this
        # reader requires, and that is not what is wrong with it (#223).
        if version > RECORD_VERSION:
            raise _NewerRecordError(
                version,
                f"{source}: this record is version {version}, newer than this SHAL "
                f"reads (up to {RECORD_VERSION}) — upgrade pyshal"
            )
        if version < 1:
            raise RecordError(f"{source}: record_version must be 1 or more")

        abort_raw = m.get("abort")
        abort = None
        if abort_raw is not None:
            a = _as_mapping(abort_raw, "abort", source)
            abort = Abort(
                by=_enum(a, "by", _ABORT_BY, source),
                after_step=_req(a, "after_step", str, source),
                reason=_req(a, "reason", str, source),
            )

        rec = cls(
            record_version=version,
            record=_id(_req(m, "record", str, source), source),
            unit=_req(m, "unit", str, source),
            station=_req(m, "station", str, source),
            sequence=_req(m, "sequence", str, source),
            sequence_version=_req(m, "sequence_version", str, source),
            firmware=_opt(m, "firmware", str, source),
            setup=_req(m, "setup", str, source),
            setup_version=_req(m, "setup_version", str, source),
            runner=_enum(m, "runner", _RUNNERS, source),
            started=_req(m, "started", str, source),
            ended=_req(m, "ended", str, source),
            steps=tuple(_step_from_mapping(s, source) for s in _seq(m, "steps", source)),
            calls=None if m.get("calls") is None else tuple(
                _as_mapping(c, "calls[]", source) for c in _seq(m, "calls", source)
            ),
            abort=abort,
        )

        stored = _enum(m, "verdict", _VERDICTS, source)
        if stored != rec.verdict:
            raise RecordError(
                f"{source}: stored verdict {stored!r} disagrees with the verdict "
                f"derived from its steps ({rec.verdict!r}) — verdict is derived, "
                f"never set by hand"
            )
        _check_plain(rec.to_mapping(), "record", source)
        return rec


def _step_to_mapping(step: Step) -> dict[str, Any]:
    return {
        "name": step.name,
        "verdict": step.verdict,
        "measurements": [_measurement_to_mapping(x) for x in step.measurements],
    }


def _measurement_to_mapping(m: Measurement) -> dict[str, Any]:
    limits: dict[str, Any] = {}
    if m.limits.min is not None:
        limits["min"] = m.limits.min
    if m.limits.max is not None:
        limits["max"] = m.limits.max
    return {
        "name": m.name,
        "value": m.value,
        "unit": m.unit,
        "limits": limits,
        "pass": m.passed,
    }


def _step_from_mapping(data: Any, source: str) -> Step:
    s = _as_mapping(data, "steps[]", source)
    return Step(
        name=_req(s, "name", str, source),
        verdict=_enum(s, "verdict", _STEP_VERDICTS, source),
        measurements=tuple(
            _measurement_from_mapping(x, source) for x in _seq(s, "measurements", source)
        ),
    )


def _measurement_from_mapping(data: Any, source: str) -> Measurement:
    x = _as_mapping(data, "measurements[]", source)
    lim = _as_mapping(x.get("limits", {}), "limits", source)
    for key in lim:
        if key not in ("min", "max"):
            raise RecordError(f"{source}: limits has unknown key {key!r} (min/max only)")
    return Measurement(
        name=_req(x, "name", str, source),
        value=_number(x, "value", source),
        unit=_req(x, "unit", str, source),
        limits=Limits(
            min=_opt_number(lim, "min", source),
            max=_opt_number(lim, "max", source),
        ),
        passed=_req(x, "pass", bool, source),
    )


# --------------------------------------------------------------------------- #
# validation helpers — messages name the key, never the value
# --------------------------------------------------------------------------- #

def _as_mapping(value: Any, what: str, source: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecordError(f"{source}: {what} must be a mapping, got {type(value).__name__}")
    for key in value:
        if not isinstance(key, str):
            raise RecordError(f"{source}: {what} has a non-string key")
    return value


def _req(m: dict[str, Any], key: str, kind: type, source: str) -> Any:
    if key not in m:
        raise RecordError(f"{source}: missing required key {key!r}")
    value = m[key]
    # bool is a subclass of int; never let one stand in for the other.
    if kind is int and isinstance(value, bool):
        raise RecordError(f"{source}: {key!r} must be {kind.__name__}, got bool")
    if not isinstance(value, kind):
        raise RecordError(f"{source}: {key!r} must be {kind.__name__}, "
                          f"got {type(value).__name__}")
    return value


def _opt(m: dict[str, Any], key: str, kind: type, source: str) -> Any:
    if m.get(key) is None:
        return None
    return _req(m, key, kind, source)


def _enum(m: dict[str, Any], key: str, allowed: frozenset[str], source: str) -> Any:
    value = _req(m, key, str, source)
    if value not in allowed:
        raise RecordError(f"{source}: {key!r} must be one of "
                          f"{', '.join(sorted(allowed))} — got {value!r}")
    return value


def _seq(m: dict[str, Any], key: str, source: str) -> list[Any]:
    if key not in m:
        raise RecordError(f"{source}: missing required key {key!r}")
    value = m[key]
    if not isinstance(value, list):
        raise RecordError(f"{source}: {key!r} must be a list, got {type(value).__name__}")
    return value


def _number(m: dict[str, Any], key: str, source: str) -> float:
    if key not in m:
        raise RecordError(f"{source}: missing required key {key!r}")
    return _finite(m[key], key, source)


def _opt_number(m: dict[str, Any], key: str, source: str) -> float | None:
    if m.get(key) is None:
        return None
    return _finite(m[key], key, source)


def _finite(value: Any, key: str, source: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RecordError(f"{source}: {key!r} must be a number, got {type(value).__name__}")
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        raise RecordError(f"{source}: {key!r} must be finite — NaN/Infinity cannot "
                          f"round-trip identically through both stores")
    return value


def _id(value: str, source: str) -> str:
    """A record id names a file; refuse anything that could name another one."""
    if not _ID_RE.fullmatch(value):
        raise RecordError(f"{source}: record id is not a safe file name "
                          f"(allowed: letters, digits, '.', '_', '-')")
    return value


def _check_plain(value: Any, where: str, source: str) -> None:
    """Refuse anything that would not survive both JSON and YAML unchanged.

    `calls` is pass-through data from AOS, so it is the one place a caller can
    smuggle in a `datetime`, a tuple or a NaN — each of which comes back a
    different type (or not at all) from one store but not the other.
    """
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, (int, float)):
        _finite(value, where, source)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_plain(item, f"{where}[{i}]", source)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise RecordError(f"{source}: {where} has a non-string key")
            _check_plain(item, f"{where}.{key}", source)
        return
    raise RecordError(f"{source}: {where} is a {type(value).__name__}, which cannot "
                      f"round-trip identically through both SQLite and YAML")


# --------------------------------------------------------------------------- #
# the two stores (`record.md` §4)
# --------------------------------------------------------------------------- #

def to_yaml(record: Record) -> str:
    """The audit copy's exact text. Deterministic for a given record."""
    return yaml.safe_dump(
        record.to_mapping(),
        sort_keys=False,          # `record.md` §2's key order, not alphabetical
        default_flow_style=False,
        allow_unicode=True,
        width=1_000_000,          # never fold a long line — folding is lossy to read
    )


def to_json(record: Record) -> str:
    """The db column's exact text. Same data, same key order, as one line."""
    return json.dumps(
        record.to_mapping(),
        sort_keys=False,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    )


def db_path(store: StoreLike) -> Path:
    """`<store>/records.db` — one file per station, in the setup's directory."""
    return Path(store) / DB_NAME


def yaml_path(store: StoreLike, record_id: str) -> Path:
    """`<store>/records/<id>.yaml` — the audit copy, beside the db."""
    return Path(store) / YAML_DIRNAME / (_id(record_id, "<argument>") + ".yaml")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    record      TEXT PRIMARY KEY,
    unit        TEXT NOT NULL,
    station     TEXT NOT NULL,
    sequence    TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    started     TEXT NOT NULL,
    record_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_unit     ON records(unit);
CREATE INDEX IF NOT EXISTS records_station  ON records(station);
CREATE INDEX IF NOT EXISTS records_sequence ON records(sequence);
CREATE INDEX IF NOT EXISTS records_verdict  ON records(verdict);
CREATE INDEX IF NOT EXISTS records_started  ON records(started);
"""


@contextlib.contextmanager
def _connect(store: StoreLike) -> Iterator[sqlite3.Connection]:
    """Open `<store>/records.db`, creating the table and indexes if absent.

    `sqlite3.Connection.__exit__` commits but does not close, so the close is
    ours to do — hence this wrapper rather than a bare `with sqlite3.connect(…)`.
    """
    path = db_path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def write(record: Record, store: StoreLike) -> None:
    """Write one record to both stores under `store` (`record.md` §3, §4).

    The YAML audit copy is written **first**, and atomically: the db is an index
    that can be rebuilt from the YAML directory, so if only one of the two
    survives a crash it should be the one that wins on disagreement.
    """
    _check_plain(record.to_mapping(), "record", record.record)
    _id(record.record, "<record>")
    text = to_yaml(record)

    path = yaml_path(store, record.record)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)

    with _connect(store) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO records "
            "(record, unit, station, sequence, verdict, started, record_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (record.record, record.unit, record.station, record.sequence,
             record.verdict, record.started, to_json(record)),
        )


@overload
def read(
    store: StoreLike,
    *,
    unit: str | None = ...,
    station: str | None = ...,
    sequence: str | None = ...,
    verdict: str | None = ...,
    newer: Literal["refuse"] = ...,
) -> list[Record]: ...


@overload
def read(
    store: StoreLike,
    *,
    unit: str | None = ...,
    station: str | None = ...,
    sequence: str | None = ...,
    verdict: str | None = ...,
    newer: Literal["skip"],
) -> tuple[list[Record], list[tuple[str, int]]]: ...


def read(
    store: StoreLike,
    *,
    unit: str | None = None,
    station: str | None = None,
    sequence: str | None = None,
    verdict: str | None = None,
    newer: Literal["refuse", "skip"] = "refuse",
) -> list[Record] | tuple[list[Record], list[tuple[str, int]]]:
    """Every record under `store`, newest last, filtered (`record.md` §6 R1).

    **The YAML wins.** Ids come from the union of the db and the `records/`
    directory, each record is loaded from its YAML file whenever one exists, and
    the filters are applied to what was loaded — never to the db's index columns.
    A stale or disagreeing db row therefore changes nothing about what comes
    back, which is what "if they disagree, the YAML wins" has to mean if it is to
    mean anything. A record with no YAML copy falls back to the db's JSON, so an
    index entry is never silently dropped.

    **A newer record** (`record_version` above `RECORD_VERSION`):

    - `newer="refuse"`, the default: the whole read raises `RecordError` (#223).
      Evidence is never silently partial.
    - `newer="skip"`, an explicit opt-in (#227) for a store shared with a station
      that upgraded first: returns a tuple `(records, skipped)` — the readable
      records, filtered and ordered as above, and `skipped`, a list of
      `(id, record_version)` for every newer record, sorted by id. The id is the
      one the store files it under (the YAML file name or the db key), not a
      field inside the record, which a newer version may have changed. A newer
      record cannot be filtered, so every one is listed whatever the filters.
      Only a newer record is skipped: a malformed one still raises.

    Any other value of `newer` raises `ValueError`.
    """
    if newer not in ("refuse", "skip"):
        raise ValueError(f"newer must be 'refuse' or 'skip', got {newer!r}")
    records: list[Record] = []
    skipped: list[tuple[str, int]] = []
    for rid in _ids(store):
        try:
            records.append(_load(store, rid))
        except _NewerRecordError as err:
            if newer != "skip":
                raise
            skipped.append((rid, err.version))
    wanted = {"unit": unit, "station": station, "sequence": sequence, "verdict": verdict}
    out = sorted(
        (r for r in records
         if all(v is None or getattr(r, k) == v for k, v in wanted.items())),
        key=lambda r: (r.started, r.record),
    )
    if newer == "skip":
        return out, skipped
    return out


def _ids(store: StoreLike) -> list[str]:
    ids: set[str] = set()
    directory = Path(store) / YAML_DIRNAME
    if directory.is_dir():
        ids.update(p.stem for p in directory.glob("*.yaml"))
    if db_path(store).exists():
        with _connect(store) as conn:
            ids.update(row[0] for row in conn.execute("SELECT record FROM records"))
    return sorted(ids)


def _load(store: StoreLike, record_id: str) -> Record:
    rec = _read_yaml(store, record_id)
    if rec is not None:
        return rec
    rec = _read_db(store, record_id)
    if rec is None:
        raise RecordError(f"{record_id}: in neither store under {Path(store)}")
    return rec


def _read_yaml(store: StoreLike, record_id: str) -> Record | None:
    """The audit copy, or None if there is no YAML file for this id."""
    path = yaml_path(store, record_id)
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            data = yaml.safe_load(fh)  # safe_load only — never yaml.load
    except (yaml.YAMLError, UnicodeDecodeError):
        raise RecordError(f"{path}: not a readable record file (corrupt or not UTF-8)") from None
    rec = Record.from_mapping(data, source=str(path))
    if rec.record != record_id:
        raise RecordError(
            f"{path}: holds record '{rec.record}' but the file name says '{record_id}'"
        )
    return rec


def _read_db(store: StoreLike, record_id: str) -> Record | None:
    """The index copy, or None if the db has no row for this id."""
    if not db_path(store).exists():
        return None
    with _connect(store) as conn:
        row = conn.execute(
            "SELECT record_json FROM records WHERE record = ?", (record_id,)
        ).fetchone()
    if row is None:
        return None
    try:
        data = json.loads(row[0])
    except (ValueError, TypeError):
        raise RecordError(
            f"{db_path(store)}:{record_id}: record_json is not valid JSON"
        ) from None
    return Record.from_mapping(data, source=f"{db_path(store)}:{record_id}")


__all__ = [
    "RECORD_VERSION", "DB_NAME", "YAML_DIRNAME",
    "Record", "Step", "Measurement", "Limits", "Abort", "RecordError",
    "Verdict", "StepVerdict", "Runner", "AbortBy",
    "write", "read", "to_yaml", "to_json", "db_path", "yaml_path",
]
