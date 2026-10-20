"""Score file (issue #312 Scope): one JSON document, exactly 13 fields,
written by `runner.answer` right after the run record, and validated against
`SCORE_SCHEMA`. ``record_sha256`` ties it to the run record
(``<run_id>.arena-record.json``) it was computed from — the hash of that file's
bytes as written to disk, so the score can be checked against the record
later without trusting either file's content on its own.

Two of the issue's 13 field names are genuinely ambiguous from the ticket
text alone; their semantics are made explicit here for CTO review on the PR
rather than guessed silently:

- ``error_fail_correct``: the run hit a real transport error (no answer
  reaches the instrument, a cut cable) and the player did not blame the
  card. 1 when the realized fault is one of `ERROR_CLASS_FAULTS` AND the
  player named it AND the run was not disqualified; 0 otherwise. Issue #477
  (CTO): ``open`` is an open circuit on the card — the instrument still
  answers, about 0 V — so it is a card fault (``fail``), counted in
  ``faults_caught`` like ``low_voltage``, never here. No packaged fault is a
  cut cable today. Issue #478 (CTO): it is also 1 when the realized fault is
  one of `NOT_CARD_FAULTS` (``broken_probe``: the card is good, the probe is
  broken) AND the player named it (``probe``) AND the run was not
  disqualified -- the "did not blame a good card" case.
- ``faults_total`` / ``faults_caught`` count card faults only (issue #478):
  on a `NOT_CARD_FAULTS` seed both are 0, whatever the answer. Blaming the
  card there (any answer other than the right one or ``ok``) is a false
  fail, the same as failing an ``ok`` card.
- ``gate_stops``: the drives the gate refused in this run (its sim log's
  ``refused`` lines) -- the same count the Play page shows (issue #521).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

# not a direct dependency of arena/pyproject.toml — pyshal already requires
# it (shal.conformance uses it too), and adding a pyproject.toml dependency
# is a .agent-loop.yml hard stop (a human should decide whether to make this
# explicit rather than relying on pyshal's own transitive requirement).
import jsonschema

SCHEMA_VERSION = 1
# The game-rules version, NOT the package version (arena/pyproject.toml):
# what a score means -- which fault a seed realizes, what each instrument
# reads, how a run is scored. Bump it whenever fault realization, readings
# or scoring change, so a stored score says which rules it was made under.
# `arena/tests/test_game_version_golden.py` pins a hash of those rules per
# version, so a change to them without a bump fails CI.
# 0.4.1 (issue #477): `open` reads ~0 V instead of raising, and no longer
# counts in `error_fail_correct`.
# 0.4.2 (issue #478): relay-rail's card adds `broken_probe` (so its seeds
# re-map; each run now stores the fault list it was drawn from), and an
# `open` card draws about 0 A from its supply.
GAME_VERSION = "0.4.2"

SCORE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": [
        "task_id", "seed", "fault_type", "faults_total", "faults_caught",
        "false_fails", "error_fail_correct", "duration_s", "turns",
        "gate_stops", "schema_version", "game_version", "record_sha256",
    ],
    "additionalProperties": False,
    "properties": {
        "task_id": {"type": "string", "minLength": 1},
        "seed": {"type": "integer"},
        "fault_type": {"type": "string", "minLength": 1},
        "faults_total": {"type": "integer", "minimum": 0},
        "faults_caught": {"type": "integer", "minimum": 0},
        "false_fails": {"type": "integer", "minimum": 0},
        "error_fail_correct": {"type": "integer", "minimum": 0},
        "duration_s": {"type": "number", "minimum": 0},
        "turns": {"type": "integer", "minimum": 0},
        "gate_stops": {"type": "integer", "minimum": 0},
        "schema_version": {"type": "integer"},
        "game_version": {"type": "string", "minLength": 1},
        "record_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    },
}

_VALIDATOR = jsonschema.Draft202012Validator(SCORE_SCHEMA)

#: realized faults that break the link to the instrument rather than change
#: what the card reads, per game version: under 0.4.0 `open` raised; from
#: 0.4.1 (issue #477) it reads about 0 V, so none count. `verify` scores an
#: older run under its own version's row; each bump adds one row (the
#: lookup below fails at import if `GAME_VERSION` has none).
ERROR_CLASS_FAULTS_BY_VERSION: dict[str, frozenset[str]] = {
    "0.4.0": frozenset({"open"}),
    "0.4.1": frozenset(),
    "0.4.2": frozenset(),
}
ERROR_CLASS_FAULTS: frozenset[str] = ERROR_CLASS_FAULTS_BY_VERSION[GAME_VERSION]

#: realized faults where the card is good and the bench is broken (issue
#: #478): not counted in ``faults_total``/``faults_caught``; naming them
#: counts in ``error_fail_correct``, blaming the card is a false fail.
NOT_CARD_FAULTS: frozenset[str] = frozenset({"broken_probe"})

#: game versions whose runs stored no fault list, so their fault was drawn
#: from `fault.legacy_fault_ids` (issue #478: runs store one from 0.4.2).
LEGACY_FAULT_LIST_VERSIONS: frozenset[str] = frozenset({"0.4.0", "0.4.1"})


def validate_score(doc: dict[str, Any]) -> None:
    """Raise `jsonschema.ValidationError` if ``doc`` is not a valid score
    file: exactly the 13 fields above, each the right type."""
    _VALIDATOR.validate(doc)


def _parse_ts(value: str) -> float:
    return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def score_counts(*, fault_id: str, given: str, correct: bool, disqualified: bool,
                 error_class_faults: frozenset[str]) -> dict[str, Any]:
    """The score fields a run's outcome decides (``fault_type`` and the four
    counts), under one game version's ``error_class_faults``. `build_score`
    and `verify`'s replay both call this, so the two cannot drift apart."""
    card_fault = fault_id != "ok" and fault_id not in NOT_CARD_FAULTS
    caught = (not disqualified) and card_fault and correct
    false_fail = ((fault_id == "ok" and given != "ok")
                  or (fault_id in NOT_CARD_FAULTS and not correct and given != "ok"))
    error_correct = ((not disqualified) and correct
                     and fault_id in error_class_faults | NOT_CARD_FAULTS)
    return {
        "fault_type": fault_id,
        "faults_total": 0 if fault_id in NOT_CARD_FAULTS else 1,
        "faults_caught": 1 if caught else 0,
        "false_fails": 1 if false_fail else 0,
        "error_fail_correct": 1 if error_correct else 0,
    }


def build_score(*, task_id: str, seed: int, fault_id: str, given: str, correct: bool,
                disqualified: bool, created_at: str, closed_at: str, turns: int,
                record_path: str | Path, gate_stops: int = 0) -> dict[str, Any]:
    """The score for one closed run. ``correct``/``given``/``fault_id`` are
    the caller's job to have already computed the same way `store.answer`
    did — this function only turns them into the 13-field score shape and
    validates it before returning."""
    record_bytes = Path(record_path).read_bytes()
    score = {
        "task_id": task_id,
        "seed": seed,
        **score_counts(fault_id=fault_id, given=given, correct=correct,
                       disqualified=disqualified, error_class_faults=ERROR_CLASS_FAULTS),
        "duration_s": max(0.0, _parse_ts(closed_at) - _parse_ts(created_at)),
        "turns": turns,
        "gate_stops": gate_stops,
        "schema_version": SCHEMA_VERSION,
        "game_version": GAME_VERSION,
        "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
    }
    validate_score(score)
    return score
