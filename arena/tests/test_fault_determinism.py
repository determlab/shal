"""issue #499: the e2e story's ``arena_fault_determinism`` check
(``dev/e2e/story.py``) follows the card's own ``faults:`` list -- it used to
hard-code ``open`` / ``3v3`` for seed 20261006, which went stale the moment a
challenge or card changed. Here, with the real package installed, its pure
stdlib replay of the card must agree with ``fault.realized_fault`` for every
packaged card, and must move when the card's faults move."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

from shal_arena.fault import realized_fault
from shal_arena.loader import load_task

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "arena" / "src" / "shal_arena" / "tasks"
_RERUN = "rerun-command-irrelevant-here"


def _load_story() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "story_for_fault_determinism", REPO_ROOT / "dev" / "e2e" / "story.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


story = _load_story()


def _card_faults(task_path: Path) -> list[dict]:
    task = yaml.safe_load(task_path.read_text("utf-8"))
    return yaml.safe_load((task_path.parent / task["card"]).read_text("utf-8"))["faults"]


def _doc(task_path: Path, seed: int) -> dict:
    rf = realized_fault(load_task(task_path).card, seed)
    return {"fault_id": rf.fault_id, "extra": rf.extra}


@pytest.mark.parametrize("task_path", sorted(TASKS.glob("*.yaml")), ids=lambda p: p.stem)
@pytest.mark.parametrize("seed", [0, 5, 20261006, 20261013])
def test_check_agrees_with_realized_fault_for_every_packaged_card(task_path: Path,
                                                                   seed: int) -> None:
    check = story.check_arena_fault_determinism(
        _doc(task_path, seed), _card_faults(task_path), seed, _RERUN)
    assert check["result"] == "pass", check["log"]


def test_check_follows_the_cards_faults_not_a_fixed_id() -> None:
    # a card whose only fault is `low_voltage`: the old hard-coded
    # `open`/`3v3` would call a correct replay a failure and a wrong one a pass.
    faults = [{"id": "low_voltage", "rail": "3v3", "shift_v": -0.40}]
    good = {"fault_id": "low_voltage", "extra": {"rail": "3v3", "shift_v": -0.40}}
    stale = {"fault_id": "open", "extra": {"rail": "3v3"}}
    assert story.check_arena_fault_determinism(good, faults, 20261006, _RERUN)["result"] == "pass"
    assert story.check_arena_fault_determinism(stale, faults, 20261006, _RERUN)["result"] == "fail"


def test_reordering_the_cards_faults_moves_the_expected_fault() -> None:
    faults = _card_faults(TASKS / "easy.yaml")
    seed = 20261013
    before = story.expected_realized_fault(faults, seed)
    after = story.expected_realized_fault(list(reversed(faults)), seed)
    assert before["fault_id"] != after["fault_id"]
    assert before == _doc(TASKS / "easy.yaml", seed)


def test_the_story_runs_against_the_newest_published_challenge() -> None:
    task_path, seed, faults = story._current_challenge()
    newest = sorted((REPO_ROOT / "arena" / "challenges").glob("*.yaml"))[-1]
    challenge = yaml.safe_load(newest.read_text("utf-8"))
    assert seed == challenge["seed"]
    assert load_task(task_path).task.id == challenge["task_id"]
    assert faults == _card_faults(task_path)
