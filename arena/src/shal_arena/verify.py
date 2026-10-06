"""Leaderboard without a server, part 1 (issue #390): replay a closed run from
the weekly seed and say whether its score file holds up.

A player opens a PR with their run's three files sitting next to each other —
``<stem>.score.json``, ``<stem>.record.json`` and ``<stem>.simlog.jsonl``
(``<stem>`` is whatever prefix the score file has: ``RunStore`` writes
``<run_id>.score.json`` and the matching ``<run_id>.record.json`` /
``<run_id>.simlog.jsonl``, so a player who commits their own ``.shal-arena/``
output already has this layout for free; the packaged sample under
``arena/tests/fixtures/verify/`` uses the empty stem, ``score.json`` /
``record.json`` / ``simlog.jsonl``, to show the simplest case). ``verify``
takes the score file's path and finds the other two beside it.

Which week: ``--week yyyy-ww`` if given; else the nearest ``yyyy-ww``-looking
ancestor directory name in the score file's own path (a real submission's
own layout, e.g. a PR's ``submissions/<week>/...``); else the CURRENT ISO
week, so the plain Agent-path invocation (no ``--week``) checks a score
file against whichever challenge is open right now.

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

Replay never executes anything from the PR: no player code runs, and no
network call is made. Every file this module opens is read-only.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .fault import realized_fault
from .loader import LoadedTask, load_task
from .score import validate_score

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
        except Exception:  # noqa: BLE001 - a malformed packaged task is not this submission's fault
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
    except json.JSONDecodeError as e:
        return _refused(f"score file is not valid JSON: {e}", week).as_dict()
    try:
        validate_score(score)
    except jsonschema.ValidationError as e:
        return _refused(f"score file does not match the score schema: {e.message}",
                        week).as_dict()

    challenge = _load_challenge(week)
    if challenge is None:
        return _refused(
            f"no challenge file for week {week!r}: {challenges_dir() / f'{week}.yaml'} "
            "does not exist", week).as_dict()

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

    record_path = _sibling(score_path, "record.json")
    if not record_path.is_file():
        return _refused(f"missing_record: {record_path} not found beside {score_path}",
                        week).as_dict()
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return _refused(f"record file is not valid JSON: {e}", week).as_dict()

    record_bytes = record_path.read_bytes()
    actual_sha256 = hashlib.sha256(record_bytes).hexdigest()
    if actual_sha256 != score["record_sha256"]:
        return _disqualified(
            f"record_sha256_mismatch: {record_path} does not hash to the score file's "
            f"record_sha256 (expected {score['record_sha256']}, got {actual_sha256})",
            week).as_dict()

    fault_id = realized_fault(loaded.card, challenge["seed"]).fault_id
    if record.get("fault_id") != fault_id:
        return _disqualified(
            f"fault_mismatch: the record claims fault {record.get('fault_id')!r}, but "
            f"seed {challenge['seed']} on task {challenge['task_id']!r} realizes "
            f"{fault_id!r}", week).as_dict()

    given = record.get("given")
    correct = given == fault_id
    if record.get("correct") != correct:
        return _disqualified(
            f"correct_mismatch: the record says correct={record.get('correct')!r}, but "
            f"given {given!r} vs fault {fault_id!r} recomputes to {correct}", week).as_dict()

    simlog_path = _sibling(score_path, "simlog.jsonl")
    entries: list[dict[str, Any]] = []
    if simlog_path.is_file():
        for line in simlog_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                entries.append(json.loads(line))
    measured = any(e.get("kind") == "measure" for e in entries)
    if not measured:
        return _disqualified(
            "no_measurements: the sim log has no 'measure' entry -- no evidence the "
            "player read an instrument before answering", week).as_dict()

    disqualified = not measured  # always False here; kept explicit for build_score parity
    caught = (not disqualified) and fault_id != "ok" and correct
    false_fail = fault_id == "ok" and given != "ok"
    error_correct = (not disqualified) and fault_id == "open" and correct
    expected = {
        "faults_caught": 1 if caught else 0,
        "false_fails": 1 if false_fail else 0,
        "error_fail_correct": 1 if error_correct else 0,
        "fault_type": fault_id,
    }
    for field, want in expected.items():
        if score[field] != want:
            return _disqualified(
                f"score_mismatch: {field} is {score[field]!r} in the score file, but the "
                f"replay gives {want!r}", week).as_dict()

    return _verified(week).as_dict()


__all__ = ["VerifyResult", "verify"]
