"""Data-only ``task.yaml`` / ``card.yaml`` (CTO ruling, issue #310 comment
5989686956): typed shapes plus the validation each document can do on its own
(no expressions, no code — a task PR cannot run anything). Cross-document
checks (a `case` that exists, wiring that names a real card input/test point,
`answer.values` matching the card's `faults` ids) live in `loader.py`, which
has both documents in hand.

Every failure here is a `TaskFormatError` that names the offending key and the
fix — the loader's one rule (DoD "a bad task file exits non-zero and the
message contains the fix")."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .errors import TaskFormatError

TASK_VERSION = 1
CARD_VERSION = 1

_ID_RE = re.compile(r"^[a-z0-9-]+$")
_FAULT_ID_RE = re.compile(r"^[a-z0-9_-]+$")
_LEVELS = frozenset(("easy", "medium", "hard"))


def _fail(msg: str, fix: str) -> None:
    raise TaskFormatError(msg, fix=fix)


def _require_dict(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        _fail(f"{where}: must be a mapping, got {type(value).__name__}",
              f"fix {where} to be a YAML mapping (key: value pairs)")
    return value


def _require_keys(d: dict, required: set[str], optional: set[str], where: str) -> None:
    unknown = set(d) - required - optional
    if unknown:
        _fail(f"{where}: unknown key {sorted(unknown)[0]!r}",
              f"remove {sorted(unknown)[0]!r} from {where}, or check for a typo; "
              f"allowed keys: {', '.join(sorted(required | optional))}")
    missing = required - set(d)
    if missing:
        _fail(f"{where}: missing required key {sorted(missing)[0]!r}",
              f"add {sorted(missing)[0]!r} to {where}")


def _require_str(d: dict, key: str, where: str, *, pattern: re.Pattern | None = None) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v:
        _fail(f"{where}.{key}: must be a non-empty string, got {v!r}",
              f"set {where}.{key} to a non-empty string")
    if pattern is not None and not pattern.match(v):
        _fail(f"{where}.{key}: {v!r} does not match {pattern.pattern}",
              f"rename {where}.{key} to match {pattern.pattern}")
    return v


def _require_number(d: dict, key: str, where: str, *, minimum: float | None = None) -> float:
    v = d.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        _fail(f"{where}.{key}: must be a number, got {v!r}",
              f"set {where}.{key} to a number")
    if minimum is not None and v < minimum:
        _fail(f"{where}.{key}: must be >= {minimum}, got {v!r}",
              f"set {where}.{key} to a number >= {minimum}")
    return v


# --------------------------------------------------------------------------- #
# task.yaml
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Instrument:
    case: str
    address: Any
    drives: str | None     # "card.<input>"
    probe: str | None      # "card.<test_point>"


@dataclass(frozen=True)
class Answer:
    kind: str                              # "enum" | "number"
    values: tuple[str, ...] = ()           # kind == "enum"
    unit: str | None = None                # kind == "number"
    tol: float | None = None               # kind == "number"


@dataclass(frozen=True)
class Question:
    text: str
    answer: Answer


@dataclass(frozen=True)
class Limits:
    max_turns: int
    max_minutes: int


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    level: str
    card: str                 # path to card.yaml, relative to the task file
    instruments: tuple[Instrument, ...]
    question: Question
    seed: int
    limits: Limits


def validate_task(doc: Any) -> Task:
    _require_dict(doc, "task")
    _require_keys(doc, required={"arena_task", "id", "title", "level", "card",
                                  "instruments", "question", "seed"},
                  optional={"limits"}, where="task")
    version = doc.get("arena_task")
    if version != TASK_VERSION:
        _fail(f"task.arena_task: this shal-arena supports version {TASK_VERSION}, "
              f"got {version!r}",
              f"set arena_task: {TASK_VERSION}, or upgrade shal-arena if the file "
              "intentionally uses a newer version")
    task_id = _require_str(doc, "id", "task", pattern=_ID_RE)
    title = _require_str(doc, "title", "task")
    level = _require_str(doc, "level", "task")
    if level not in _LEVELS:
        _fail(f"task.level: must be one of {sorted(_LEVELS)}, got {level!r}",
              f"set task.level to one of {sorted(_LEVELS)}")
    card = _require_str(doc, "card", "task")
    instruments = _validate_instruments(doc.get("instruments"))
    question = _validate_question(doc.get("question"))
    seed = doc.get("seed")
    if isinstance(seed, bool) or not isinstance(seed, int):
        _fail(f"task.seed: must be an integer, got {seed!r}", "set task.seed to an integer")
    limits = _validate_limits(doc.get("limits", {"max_turns": 60, "max_minutes": 30}))
    return Task(id=task_id, title=title, level=level, card=card, instruments=instruments,
                question=question, seed=seed, limits=limits)


def _validate_instruments(value: Any) -> tuple[Instrument, ...]:
    if not isinstance(value, list) or not value:
        _fail(f"task.instruments: must be a non-empty list, got {value!r}",
              "add at least one instrument under task.instruments")
    out = []
    for i, entry in enumerate(value):
        where = f"task.instruments[{i}]"
        _require_dict(entry, where)
        if "replacement_usd" in entry:
            _fail(f"{where}.replacement_usd: not allowed in a task file",
                  f"remove replacement_usd from {where}; it lives only in "
                  "card_sim/catalogue/instruments.yaml, keyed by case")
        _require_keys(entry, required={"case", "address"},
                      optional={"drives", "probe"}, where=where)
        case = _require_str(entry, "case", where)
        address = entry.get("address")
        if address is None or isinstance(address, bool) or not isinstance(address, (str, int)):
            _fail(f"{where}.address: must be a string or integer, got {address!r}",
                  f"set {where}.address to the instrument's bus address")
        has_drives = "drives" in entry
        has_probe = "probe" in entry
        if has_drives == has_probe:
            _fail(f"{where}: must have exactly one of 'drives' or 'probe'",
                  f"add exactly one of 'drives'/'probe' to {where}")
        wiring_key = "drives" if has_drives else "probe"
        wiring = _require_str(entry, wiring_key, where)
        if not wiring.startswith("card."):
            _fail(f"{where}.{wiring_key}: must name a card.<name>, got {wiring!r}",
                  f"set {where}.{wiring_key} to 'card.<input or test point>'")
        out.append(Instrument(case=case, address=address,
                               drives=wiring if has_drives else None,
                               probe=wiring if has_probe else None))
    return tuple(out)


def _validate_question(value: Any) -> Question:
    _require_dict(value, "task.question")
    _require_keys(value, required={"text", "answer"}, optional=set(), where="task.question")
    text = _require_str(value, "text", "task.question")
    answer_doc = _require_dict(value["answer"], "task.question.answer")
    kind = _require_str(answer_doc, "kind", "task.question.answer")
    if kind == "enum":
        _require_keys(answer_doc, required={"kind", "values"}, optional=set(),
                      where="task.question.answer")
        values = answer_doc.get("values")
        if (not isinstance(values, list) or not values
                or not all(isinstance(v, str) and v for v in values)):
            _fail("task.question.answer.values: must be a non-empty list of non-empty "
                  f"strings, got {values!r}",
                  "set task.question.answer.values to the card's fault ids")
        if len(set(values)) != len(values):
            _fail(f"task.question.answer.values: duplicate value in {values!r}",
                  "remove the duplicate from task.question.answer.values")
        return Question(text=text, answer=Answer(kind="enum", values=tuple(values)))
    if kind == "number":
        _require_keys(answer_doc, required={"kind", "unit", "tol"}, optional=set(),
                      where="task.question.answer")
        unit = _require_str(answer_doc, "unit", "task.question.answer")
        tol = _require_number(answer_doc, "tol", "task.question.answer", minimum=0)
        return Question(text=text, answer=Answer(kind="number", unit=unit, tol=tol))
    _fail(f"task.question.answer.kind: must be 'enum' or 'number', got {kind!r}",
          "set task.question.answer.kind to 'enum' or 'number'")
    raise AssertionError("unreachable")  # _fail always raises


def _validate_limits(value: Any) -> Limits:
    _require_dict(value, "task.limits")
    _require_keys(value, required={"max_turns", "max_minutes"}, optional=set(),
                  where="task.limits")
    max_turns = _require_number(value, "max_turns", "task.limits", minimum=1)
    max_minutes = _require_number(value, "max_minutes", "task.limits", minimum=1)
    return Limits(max_turns=int(max_turns), max_minutes=int(max_minutes))


# --------------------------------------------------------------------------- #
# card.yaml
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Input:
    name: str
    nominal_v: float


@dataclass(frozen=True)
class Rail:
    name: str
    from_input: str
    nominal_v: float
    tol_pct: float
    test_point: str
    min_input_v: float


@dataclass(frozen=True)
class Damage:
    input: str
    above_v: float
    effect: str
    source: str


@dataclass(frozen=True)
class Fault:
    id: str
    extra: dict = field(default_factory=dict)   # fault-specific params (shift_v, ...): out of
                                                 # scope for this ticket (no fault injection yet)


@dataclass(frozen=True)
class Card:
    id: str
    description: str
    inputs: tuple[Input, ...]
    rails: tuple[Rail, ...]
    damage: tuple[Damage, ...]
    faults: tuple[Fault, ...]


def validate_card(doc: Any) -> Card:
    _require_dict(doc, "card")
    _require_keys(doc, required={"arena_card", "id", "description", "inputs", "rails",
                                  "damage", "faults"}, optional=set(), where="card")
    version = doc.get("arena_card")
    if version != CARD_VERSION:
        _fail(f"card.arena_card: this shal-arena supports version {CARD_VERSION}, "
              f"got {version!r}",
              f"set arena_card: {CARD_VERSION}, or upgrade shal-arena if the file "
              "intentionally uses a newer version")
    card_id = _require_str(doc, "id", "card", pattern=_ID_RE)
    description = _require_str(doc, "description", "card")
    inputs = _validate_inputs(doc.get("inputs"))
    input_names = {i.name for i in inputs}
    rails = _validate_rails(doc.get("rails"), input_names)
    damage = _validate_damage(doc.get("damage"), input_names)
    faults = _validate_faults(doc.get("faults"))
    return Card(id=card_id, description=description, inputs=inputs, rails=rails,
                damage=damage, faults=faults)


def _validate_inputs(value: Any) -> tuple[Input, ...]:
    d = _require_dict(value, "card.inputs")
    if not d:
        _fail("card.inputs: must declare at least one input", "add an entry under card.inputs")
    out = []
    for name, entry in d.items():
        where = f"card.inputs.{name}"
        _require_dict(entry, where)
        _require_keys(entry, required={"nominal_v"}, optional=set(), where=where)
        out.append(Input(name=name, nominal_v=_require_number(entry, "nominal_v", where)))
    return tuple(out)


def _validate_rails(value: Any, input_names: set[str]) -> tuple[Rail, ...]:
    d = _require_dict(value, "card.rails")
    if not d:
        _fail("card.rails: must declare at least one rail", "add an entry under card.rails")
    out = []
    seen_test_points: dict[str, str] = {}
    for name, entry in d.items():
        where = f"card.rails.{name}"
        _require_dict(entry, where)
        _require_keys(entry, required={"from", "nominal_v", "tol_pct", "test_point",
                                        "min_input_v"}, optional=set(), where=where)
        from_input = _require_str(entry, "from", where)
        if from_input not in input_names:
            _fail(f"{where}.from: {from_input!r} is not a card.inputs name",
                  f"set {where}.from to one of {sorted(input_names)}")
        test_point = _require_str(entry, "test_point", where)
        if test_point in seen_test_points:
            _fail(f"{where}.test_point: {test_point!r} is already used by "
                  f"card.rails.{seen_test_points[test_point]}",
                  f"give {where}.test_point a name unique across card.rails")
        seen_test_points[test_point] = name
        out.append(Rail(name=name, from_input=from_input,
                        nominal_v=_require_number(entry, "nominal_v", where),
                        tol_pct=_require_number(entry, "tol_pct", where, minimum=0),
                        test_point=test_point,
                        min_input_v=_require_number(entry, "min_input_v", where)))
    return tuple(out)


def _validate_damage(value: Any, input_names: set[str]) -> tuple[Damage, ...]:
    if not isinstance(value, list):
        _fail(f"card.damage: must be a list, got {value!r}", "set card.damage to a list")
    out = []
    for i, entry in enumerate(value):
        where = f"card.damage[{i}]"
        _require_dict(entry, where)
        _require_keys(entry, required={"input", "above_v", "effect", "source"},
                      optional=set(), where=where)
        input_name = _require_str(entry, "input", where)
        if input_name not in input_names:
            _fail(f"{where}.input: {input_name!r} is not a card.inputs name",
                  f"set {where}.input to one of {sorted(input_names)}")
        # "a threshold without a documented source is not counted as damage" (CTO
        # ruling) — enforced as a hard validation error, not a silent drop, so a
        # card author sees the fix immediately rather than losing the threshold.
        out.append(Damage(input=input_name,
                          above_v=_require_number(entry, "above_v", where),
                          effect=_require_str(entry, "effect", where),
                          source=_require_str(entry, "source", where)))
    return tuple(out)


def _validate_faults(value: Any) -> tuple[Fault, ...]:
    if not isinstance(value, list) or not value:
        _fail(f"card.faults: must be a non-empty list, got {value!r}",
              "add at least one fault (an 'ok' entry plus the faults the task can pick)")
    out = []
    seen: set[str] = set()
    for i, entry in enumerate(value):
        where = f"card.faults[{i}]"
        _require_dict(entry, where)
        fault_id = _require_str(entry, "id", where, pattern=_FAULT_ID_RE)
        if fault_id in seen:
            _fail(f"{where}.id: duplicate fault id {fault_id!r}",
                  f"give {where} a unique id")
        seen.add(fault_id)
        out.append(Fault(id=fault_id, extra={k: v for k, v in entry.items() if k != "id"}))
    return tuple(out)
