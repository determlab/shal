"""Reads one ``task.yaml`` (and the ``card.yaml`` it points at), validates both,
and cross-checks them against each other and against the packaged ADK cases —
the loader's one rule (CTO ruling, issue #310): every failure names the key
and the fix.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .cases import resolve_case
from .errors import TaskFormatError
from .schema import Card, Task, validate_card, validate_task


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
