"""Reads one ``task.yaml`` (and the ``card.yaml`` it points at), validates both,
and cross-checks them against each other and against the packaged ADK cases —
the loader's one rule (CTO ruling, issue #310): every failure names the key
and the fix.

``once_per_path`` (issue #394) is unrelated to task/card loading but lives
here anyway: it is the same shape of problem (load a file-backed thing,
exactly once, keyed by its resolved path), and has no other natural home
that is not ``runner.py`` itself, which this ticket's fence keeps untouched.
"""
from __future__ import annotations

import functools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

import yaml

from .cases import resolve_case
from .errors import TaskFormatError
from .schema import Card, Task, validate_card, validate_task

_T = TypeVar("_T")


def once_per_path(fn: Callable[[str | Path], _T]) -> Callable[[str | Path], _T]:
    """Wrap *fn* (a callable taking one path-like argument) so repeated calls
    for the SAME resolved path return the SAME result, never calling *fn*
    again for it (issue #394).

    Why this exists: ``shal-arena bench`` plays many runs against one
    ``driver.py``. The runner's own import of it (``runner._import_driver_file``)
    re-executes the file's top level on every call, which defines a BRAND NEW
    class object each time — the registry sees that as a second, distinct
    candidate for the same ``compatible`` ("claimed by 2 drivers"), even
    though it is the same file, forcing a player to add ``override=True`` to
    their own ``driver.py`` as a workaround. Caching the import, keyed by
    resolved path, means the class object's identity is stable across every
    run in one benchmark, so the registry's own "re-registering the SAME
    class is a no-op" rule (``registry.py`` — unchanged by this) is what
    actually applies, same as it would for a driver imported once by hand.

    A fresh cache every call to ``once_per_path`` itself — never shared
    across callers, so one ``bench`` run's cache cannot leak into another's
    or into a different test."""
    cache: dict[str, _T] = {}

    @functools.wraps(fn)
    def wrapped(path: str | Path) -> _T:
        key = str(Path(path).resolve())
        if key not in cache:
            cache[key] = fn(path)
        return cache[key]

    return wrapped


@dataclass(frozen=True)
class LoadedTask:
    task: Task
    card: Card
    task_path: Path
    card_path: Path


def _read_yaml(path: Path, where: str) -> Any:
    if not path.is_file():
        raise TaskFormatError(
            f"{where} not found: {path}",
            fix=f"pass the path to an existing {where}, or run `shal-arena run --help`")
    try:
        with path.open("r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise TaskFormatError(f"{where}: invalid YAML: {e}",
                              fix=f"fix the YAML syntax in {path}") from e


_PACKAGED_TASKS_DIR = Path(__file__).resolve().parent / "tasks"
_LEVEL_ORDER = {"easy": 0, "medium": 1, "hard": 2}


def list_tasks() -> list[dict[str, str]]:
    """The packaged tasks (package data, issue #416): ``name`` (the file stem,
    what ``shal-arena run <name>`` takes), ``path`` and ``level``."""
    tasks = []
    for path in _PACKAGED_TASKS_DIR.glob("*.yaml"):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        tasks.append({"name": path.stem, "path": str(path), "level": str(doc.get("level", ""))})
    tasks.sort(key=lambda t: (_LEVEL_ORDER.get(t["level"], len(_LEVEL_ORDER)), t["name"]))
    return tasks


def resolve_task(name_or_path: str | Path) -> Path:
    """A task file path as given, else a packaged task by name (issue #416)."""
    path = Path(name_or_path)
    if path.is_file():
        return path
    packaged = {t["name"]: t["path"] for t in list_tasks()}
    if str(name_or_path) in packaged:
        return Path(packaged[str(name_or_path)])
    raise TaskFormatError(
        f"no task file or packaged task named {str(name_or_path)!r}; "
        f"valid names: {', '.join(packaged)}",
        fix="pick one of the valid names, or run `shal-arena tasks --json` to list them")


def load_task(task_path: str | Path) -> LoadedTask:
    """Load, validate and cross-check ``task_path`` and the ``card.yaml`` it
    names. Raises `TaskFormatError` naming the key and the fix on any problem."""
    task_path = Path(task_path)
    task_doc = _read_yaml(task_path, "task file")
    task = validate_task(task_doc)

    card_path = (task_path.parent / task.card).resolve()
    card_doc = _read_yaml(card_path, "card file")
    card = validate_card(card_doc)

    _cross_check(task, card, task_path)
    return LoadedTask(task=task, card=card, task_path=task_path, card_path=card_path)


def _cross_check(task: Task, card: Card, task_path: Path) -> None:
    input_names = {i.name for i in card.inputs}
    test_points = {r.test_point for r in card.rails} | {t.test_point for t in card.temp_points}

    for i, instrument in enumerate(task.instruments):
        where = f"{task_path}: task.instruments[{i}]"
        case = resolve_case(instrument.case)  # raises TaskFormatError naming the known cases
        # CTO review of #479: `switches` skips `call`'s damage check (runner
        # `call_op`), so it is only allowed on a power-switch case; `drives`
        # is not allowed there, so each key matches one kind of case.
        if instrument.switches is not None and not case.power_switch:
            raise TaskFormatError(
                f"{where}.switches: case {instrument.case!r} is not a power switch, "
                f"so it cannot use `switches`",
                fix=f"use `drives: {instrument.switches}` for case {instrument.case!r}")
        if instrument.drives is not None and case.power_switch:
            raise TaskFormatError(
                f"{where}.drives: case {instrument.case!r} is a power switch, "
                f"so it cannot use `drives`",
                fix=f"use `switches: {instrument.drives}` for case {instrument.case!r}")
        if instrument.drives is not None or instrument.switches is not None:
            # issue #473: `switches` names a card input, checked like `drives`
            key = "drives" if instrument.drives is not None else "switches"
            value = instrument.drives if key == "drives" else instrument.switches
            name = value.removeprefix("card.")
            if name not in input_names:
                raise TaskFormatError(
                    f"{where}.{key}: {value!r} is not a card input",
                    fix=f"set {key} to one of {sorted('card.' + n for n in input_names)}")
        else:
            name = instrument.probe.removeprefix("card.")
            if name not in test_points:
                raise TaskFormatError(
                    f"{where}.probe: {instrument.probe!r} is not a card test point",
                    fix=f"set probe to one of {sorted('card.' + t for t in test_points)}")

    if task.question.answer.kind == "enum":
        fault_ids = {f.id for f in card.faults}
        answer_values = set(task.question.answer.values)
        if answer_values != fault_ids:
            raise TaskFormatError(
                f"{task_path}: task.question.answer.values {sorted(answer_values)} must "
                f"equal the card's fault ids {sorted(fault_ids)}",
                fix=f"set task.question.answer.values to {sorted(fault_ids)}")
