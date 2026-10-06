"""Score file (issue #312 Scope): one JSON document, exactly 13 fields,
written by `runner.answer` right after the run record, and validated against
`SCORE_SCHEMA`. ``record_sha256`` ties it to the run record
(``<run_id>.arena-record.json``) it was computed from — the hash of that file's
bytes as written to disk, so the score can be checked against the record
later without trusting either file's content on its own.

Two of the issue's 13 field names are genuinely ambiguous from the ticket
text alone; their semantics are made explicit here for CTO review on the PR
rather than guessed silently:

- ``error_fail_correct``: credit for correctly naming an ERROR-class fault —
  today only ``open`` (no answer reaches the instrument at all; see
  ``fault.harness_for_run``'s "extends `fault: unplugged`") — as opposed to a
  VALUE-class fault (``low_voltage``, ``noise``), which changes a reading
  rather than breaking the link. 1 when the realized fault is ``open`` AND
  the player named it AND the run was not disqualified; 0 otherwise.
- ``gate_stops``: 0 for every run this ticket can produce. `shal-arena`
  invokes no gated (``config``/``actuator``) op of its own yet — wired for a
  later arena ticket that plays a task through SHAL's own approval gate.
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
# shal-arena's own version (arena/pyproject.toml). Kept a literal, like
# test_packaging.py's own version pin, rather than imported at runtime: a
# mismatch is caught by that same test, not hidden behind an import.
GAME_VERSION = "0.4.0"

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


def validate_score(doc: dict[str, Any]) -> None:
    """Raise `jsonschema.ValidationError` if ``doc`` is not a valid score
    file: exactly the 13 fields above, each the right type."""
    _VALIDATOR.validate(doc)


def _parse_ts(value: str) -> float:
    return time.mktime(time.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))


def build_score(*, task_id: str, seed: int, fault_id: str, given: str, correct: bool,
                disqualified: bool, created_at: str, closed_at: str, turns: int,
                record_path: str | Path) -> dict[str, Any]:
    """The score for one closed run. ``correct``/``given``/``fault_id`` are
    the caller's job to have already computed the same way `store.answer`
    did — this function only turns them into the 13-field score shape and
    validates it before returning."""
    caught = (not disqualified) and fault_id != "ok" and correct
    false_fail = fault_id == "ok" and given != "ok"
    error_correct = (not disqualified) and fault_id == "open" and correct
    record_bytes = Path(record_path).read_bytes()
    score = {
        "task_id": task_id,
        "seed": seed,
        "fault_type": fault_id,
        "faults_total": 1,
        "faults_caught": 1 if caught else 0,
        "false_fails": 1 if false_fail else 0,
        "error_fail_correct": 1 if error_correct else 0,
        "duration_s": max(0.0, _parse_ts(closed_at) - _parse_ts(created_at)),
        "turns": turns,
        "gate_stops": 0,
        "schema_version": SCHEMA_VERSION,
        "game_version": GAME_VERSION,
        "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
    }
    validate_score(score)
    return score
