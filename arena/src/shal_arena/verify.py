"""Leaderboard without a server, part 1 (issue #390): replay a closed run from
the weekly seed and say whether its score file holds up.

A player opens a PR with their run's three files sitting next to each other —
``<stem>.score.json``, ``<stem>.arena-record.json`` and ``<stem>.simlog.jsonl``
(``<stem>`` is whatever prefix the score file has: ``RunStore`` writes
``<run_id>.score.json`` and the matching ``<run_id>.arena-record.json`` /
``<run_id>.simlog.jsonl``, so a player who commits their own ``.shal-arena/``
output already has this layout for free; the packaged sample under
``arena/tests/fixtures/verify/`` uses the empty stem, ``score.json`` /
``arena-record.json`` / ``simlog.jsonl``, to show the simplest case).
``verify`` takes the score file's path and finds the other two beside it. A
capture made before issue #435's rename has ``<stem>.record.json`` instead;
``verify`` reads that name when the new one is absent.

Which week: ``--week yyyy-ww`` if given; else the nearest ``yyyy-ww``-looking
ancestor directory name in the score file's own path (a real submission's
own layout, e.g. a PR's ``submissions/<week>/...``); else the CURRENT ISO
week, so the plain Agent-path invocation (no ``--week``) checks a score
file against whichever challenge is open right now.

What verify proves, and what it cannot (CTO review on #393): a verified
submission is CONSISTENCY-CHECKED, NOT PROOF OF A RUN. verify proves only
that the files agree with each other and with the published seed: the record
hashes to ``record_sha256``, the realized fault recomputed from the seed
matches the record, the answer matches that fault, the score fields recompute,
and the sim log has at least one ``measure`` line. It cannot prove that
anyone ran the sim: the seed is public and the code is open, so
``realized_fault(card, seed)`` gives the answer offline, and a hand-written
record with that answer plus one ``{"kind": "measure"}`` line verifies.
``turns``, ``duration_s`` and ``gate_stops`` are only schema-checked --
nothing recomputes them -- so a leaderboard must never rank on them.

Trust model (the sim is open-source, so only the keys below are load-bearing):

- The week's ``task_id`` and ``seed`` come ONLY from ``arena/challenges/
  <week>.yaml`` -- a file this repo publishes, never from the PR. A
  submission naming the wrong seed (or the wrong task for that week) is
  ``refused`` before any replay runs at all.
- The task and card loaded for replay come ONLY from this repo's own
  ``arena/src/shal_arena/tasks/`` (matched by ``task_id``), never from the
  PR's own ``record.json["task_path"]`` / ``["card_path"]`` -- those two
  fields are read back only to report, never to decide what to load. A
  forged path in a submitted record can point nowhere this function will
  ever open.
- The realized fault for the week is recomputed from the trusted seed and
  card via ``fault.realized_fault`` -- the SAME pure, seed-only computation
  ``runner.pick_fault`` makes for a live run, so it gives byte-identical
  results on Windows, Linux and macOS (``random.Random`` has no OS-dependent
  state). The submitted ``record.json["fault_id"]`` is compared against
  this, never trusted on its own.
- ``record_sha256`` in the score file is checked against the ACTUAL bytes of
  the sibling ``record.json`` on disk: an edited record (a changed
  ``given``, a changed ``fault_id``) changes those bytes, so editing one
  without recomputing the hash is caught here; editing both still fails
  because the fault_id this function trusts is recomputed independently of
  the record, not read from it (the two checks are deliberately redundant).
- Disqualification: at least one ``"kind": "measure"`` entry anywhere in the
  sim log. This is a simpler rule than ``runner.answer``'s own (which checks
  one specific probe address) -- deliberate, because a replay has no live
  instrument list to check addresses against, only the log; flagged here
  for CTO review the same way ``score.py``'s own ambiguous fields are.
- The score is recomputed under the rules of its own ``game_version``
  (``score.ERROR_CLASS_FAULTS_BY_VERSION``, through the same
  ``score.score_counts`` ``build_score`` uses). The fault list the seed draws
  from also follows the version, never the record: a 0.4.0/0.4.1 run draws
  from ``fault.legacy_fault_ids`` (the card's faults as at 0.4.1), a 0.4.2
  run from the card's packaged list (issue #478). A version not in that table
  is ``refused`` / ``unknown_game_version``. ``game_version`` sits in the
  score file, which ``record_sha256`` does not cover, so the label alone is
  not trusted: the week's challenge file lists the versions it accepts
  (``game_versions``), and a score whose version is not on that list is
  ``refused`` / ``wrong_game_version``. A run relabelled to an older version
  to gain a point still has to recompute under that version's rules.

Replay never executes anything from the PR: no player code runs, and no
network call is made. Every file this module opens is read-only.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .fault import answer_for, legacy_fault_ids, realized_fault
from .loader import LoadedTask, load_task
from .score import (
    ERROR_CLASS_FAULTS_BY_VERSION,
    LEGACY_FAULT_LIST_VERSIONS,
    score_counts,
    validate_score,
)

# TASKS_DIR travels WITH the installed package (same pattern runner.py's own
# task resolution already uses) -- a player needs no source checkout to run
# a task, only the installed shal-arena wheel.
TASKS_DIR = Path(__file__).resolve().parent / "tasks"


def challenges_dir() -> Path:
    """``arena/challenges/`` is NOT packaged: it is a repo artifact this org
    publishes each week, not part of the product. Resolved from the CURRENT
    working directory (read fresh on every call, never cached) -- a player
    (and issue #391's GitHub Action) runs ``shal-arena verify`` from a
    checkout of this repo, same as ``git clone`` + ``cd``; ``__file__``
    would resolve to wherever the wheel happened to install, which has no
    ``arena/challenges/`` at all once shal-arena is a real installed
    package rather than ``pip install -e``."""
    return Path.cwd() / "arena" / "challenges"

_log = logging.getLogger(__name__)
_WEEK_RE = re.compile(r"^\d{4}-\d{2}$")
_SCORE_SUFFIX = "score.json"  # no leading dot: "run-x.score.json" keeps its own dot in the prefix


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    result: str              # "verified" | "disqualified" | "refused"
    reason: str
    week: str | None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "result": self.result, "reason": self.reason,
                "week": self.week, "side_effect": "none"}


def _refused(reason: str, week: str | None = None) -> VerifyResult:
    return VerifyResult(ok=False, result="refused", reason=reason, week=week)


def _disqualified(reason: str, week: str | None) -> VerifyResult:
    return VerifyResult(ok=False, result="disqualified", reason=reason, week=week)


def _verified(week: str) -> VerifyResult:
    return VerifyResult(ok=True, result="verified", reason="ok", week=week)


def _sibling(score_path: Path, suffix: str) -> Path:
    """``<prefix>score.json`` -> ``<prefix><suffix>``, same directory --
    ``prefix`` keeps whatever trailing dot it already has, so
    ``run-x.score.json`` gives ``run-x.record.json`` and the bare
    ``score.json`` gives ``record.json``, both from the same rule."""
    name = score_path.name
    prefix = name[: -len(_SCORE_SUFFIX)] if name.endswith(_SCORE_SUFFIX) else name
    return score_path.parent / f"{prefix}{suffix}"


def _current_week() -> str:
    year, week, _weekday = datetime.date.today().isocalendar()
    return f"{year}-{week:02d}"


def _default_week(score_path: Path) -> str:
    """The week to check against when ``--week`` is not given: the nearest
    ancestor directory name that looks like an ISO week (``yyyy-ww``),
    searched from the file's own directory upward -- a real submission's own
    layout, e.g. a PR's ``submissions/<week>/...`` -- else THIS week, so the
    plain Agent-path invocation (no ``--week``) checks a score file against
    the challenge that is actually open right now."""
    for part in (score_path.resolve()).parts[::-1]:
        if _WEEK_RE.match(part):
            return part
    return _current_week()


def _load_challenge(week: str) -> dict[str, Any] | None:
    path = challenges_dir() / f"{week}.yaml"
    if not path.is_file():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _resolve_task(task_id: str) -> LoadedTask | None:
    """The LoadedTask whose ``task.id`` is ``task_id``, from this repo's own
    packaged tasks -- never from anything the PR named."""
    for path in sorted(TASKS_DIR.glob("*.yaml")):
        try:
            loaded = load_task(path)
        except Exception as e:  # noqa: BLE001 - a malformed packaged task is not this submission's fault
            _log.warning("skipping packaged task %s: %s", path.name, e)
            continue
        if loaded.task.id == task_id:
            return loaded
    return None


def verify(score_path: str | Path, *, week: str | None = None) -> dict[str, Any]:
    """Replay the run ``score_path`` claims and say whether it holds up.

    Returns the Agent-path JSON shape directly (``ok``, ``result``,
    ``reason``, ``week``, ``side_effect: "none"``); never raises for a bad
    submission -- a malformed or inconsistent one comes back ``refused`` or
    ``disqualified``, not an exception. See the module docstring for the
    trust model and the ``week`` default."""
    score_path = Path(score_path)
    week = week or _default_week(score_path)

    if not score_path.is_file():
        return _refused(f"score file not found: {score_path}", week).as_dict()
    try:
        score = json.loads(score_path.read_text(encoding="utf-8"))
    except ValueError as e:  # JSONDecodeError, or bytes that are not UTF-8
        return _refused(f"score file is not valid JSON: {e}", week).as_dict()
    try:
        validate_score(score)
    except jsonschema.ValidationError as e:
        return _refused(f"score file does not match the score schema: {e.message}",
                        week).as_dict()

    game_version = score["game_version"]
    error_class_faults = ERROR_CLASS_FAULTS_BY_VERSION.get(game_version)
    if error_class_faults is None:
        known = ", ".join(sorted(ERROR_CLASS_FAULTS_BY_VERSION))
        return _refused(
            f"unknown_game_version: the score was made under game {game_version!r}, "
            f"this verify knows only {known} -- re-run the challenge with a listed "
            "shal-arena version", week).as_dict()

    challenge = _load_challenge(week)
    if challenge is None:
        return _refused(
            f"no challenge file for week {week!r}: {challenges_dir() / f'{week}.yaml'} "
            "does not exist", week).as_dict()

    allowed = challenge.get("game_versions") or []
    if game_version not in allowed:
        return _refused(
            f"wrong_game_version: game {game_version!r} is not on week {week}'s list "
            f"{allowed!r} in arena/challenges/{week}.yaml -- re-run the challenge with "
            "one of those versions", week).as_dict()

    if score["seed"] != challenge["seed"]:
        return _refused(
            f"wrong_seed: score seed {score['seed']} does not match week {week}'s seed "
            f"{challenge['seed']} in arena/challenges/{week}.yaml", week).as_dict()
    if score["task_id"] != challenge["task_id"]:
        return _refused(
            f"wrong_task: score task_id {score['task_id']!r} does not match week {week}'s "
            f"task_id {challenge['task_id']!r} in arena/challenges/{week}.yaml",
            week).as_dict()

    loaded = _resolve_task(challenge["task_id"])
    if loaded is None:
        return _refused(
            f"unknown_task_id: {challenge['task_id']!r} (named by arena/challenges/"
            f"{week}.yaml) matches no task under {TASKS_DIR}", week).as_dict()

    record_path = _sibling(score_path, "arena-record.json")
    if not record_path.is_file() and _sibling(score_path, "record.json").is_file():
        record_path = _sibling(score_path, "record.json")  # pre-#435 capture
    if not record_path.is_file():
        return _refused(f"missing_record: {record_path} not found beside {score_path}",
                        week).as_dict()
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except ValueError as e:
        return _refused(f"record file is not valid JSON: {e}", week).as_dict()
    if not isinstance(record, dict):
        return _refused(f"record file is not a JSON object: {record_path}", week).as_dict()

    record_bytes = record_path.read_bytes()
    actual_sha256 = hashlib.sha256(record_bytes).hexdigest()
    if actual_sha256 != score["record_sha256"]:
        return _disqualified(
            f"record_sha256_mismatch: {record_path} does not hash to the score file's "
            f"record_sha256 (expected {score['record_sha256']}, got {actual_sha256})",
            week).as_dict()

    # the fault list comes from the game version, never from the record:
    # a pre-0.4.2 run was drawn from the card's list as it stood at 0.4.1,
    # a 0.4.2 run from the card's full packaged list (issue #478).
    if game_version in LEGACY_FAULT_LIST_VERSIONS:
        fault_ids: list[str] | None = legacy_fault_ids(loaded.card)
    else:
        fault_ids = None
    fault_id = realized_fault(loaded.card, challenge["seed"], fault_ids).fault_id
    if record.get("fault_id") != fault_id:
        return _disqualified(
            f"fault_mismatch: the record claims fault {record.get('fault_id')!r}, but "
            f"seed {challenge['seed']} on task {challenge['task_id']!r} realizes "
            f"{fault_id!r}", week).as_dict()

    given = record.get("given")
    expected_answer = answer_for(loaded.card, fault_id)
    correct = given == expected_answer
    if record.get("correct") != correct:
        return _disqualified(
            f"correct_mismatch: the record says correct={record.get('correct')!r}, but "
            f"given {given!r} vs answer {expected_answer!r} (fault {fault_id!r}) "
            f"recomputes to {correct}", week).as_dict()

    simlog_path = _sibling(score_path, "simlog.jsonl")
    entries: list[dict[str, Any]] = []
    try:
        simlog_text = simlog_path.read_text(encoding="utf-8") if simlog_path.is_file() else ""
    except UnicodeDecodeError as e:
        return _refused(f"simlog_invalid_json: {simlog_path} is not UTF-8 ({e}) -- submit the "
                        "sim log exactly as shal-arena wrote it", week).as_dict()
    for n, line in enumerate(simlog_text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            return _refused(
                f"simlog_invalid_json: {simlog_path} line {n} is not valid JSON ({e}) -- "
                "submit the sim log exactly as shal-arena wrote it", week).as_dict()
        if not isinstance(entry, dict):
            return _refused(
                f"simlog_invalid_json: {simlog_path} line {n} is not a JSON object -- "
                "submit the sim log exactly as shal-arena wrote it", week).as_dict()
        entries.append(entry)
    measured = any(e.get("kind") == "measure" for e in entries)
    if not measured:
        return _disqualified(
            "no_measurements: the sim log has no 'measure' entry -- no evidence the "
            "player read an instrument before answering", week).as_dict()

    disqualified = not measured  # always False here; kept explicit for build_score parity
    expected = score_counts(fault_id=fault_id, given=given, correct=correct,
                            disqualified=disqualified, error_class_faults=error_class_faults)
    for field, want in expected.items():
        if score[field] != want:
            return _disqualified(
                f"score_mismatch: {field} is {score[field]!r} in the score file, but the "
                f"replay gives {want!r}", week).as_dict()

    return _verified(week).as_dict()


__all__ = ["VerifyResult", "verify"]
