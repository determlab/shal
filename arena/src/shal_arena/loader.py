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
    test_points = {r.test_point for r in card.rails}

    for i, instrument in enumerate(task.instruments):
        where = f"{task_path}: task.instruments[{i}]"
        resolve_case(instrument.case)  # raises TaskFormatError naming the known cases
        if instrument.drives is not None:
            name = instrument.drives.removeprefix("card.")
            if name not in input_names:
                raise TaskFormatError(
                    f"{where}.drives: {instrument.drives!r} is not a card input",
                    fix=f"set drives to one of {sorted('card.' + n for n in input_names)}")
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
