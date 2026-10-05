"""`load_task` / `validate_task` / `validate_card` (issue #310, CTO ruling
5989686956): every malformed file exits with a message naming the key and a
non-empty fix."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shal_arena.errors import TaskFormatError
from shal_arena.loader import load_task
from shal_arena.schema import validate_card

from .conftest import SAMPLE_TASK

_CARDS_DIR = Path(__file__).resolve().parents[1] / "src" / "shal_arena" / "cards"


def test_task_file_with_replacement_usd_is_rejected(minimal_task: Path) -> None:
    doc = yaml.safe_load(minimal_task.read_text(encoding="utf-8"))
    doc["instruments"][0]["replacement_usd"] = 100
    minimal_task.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "replacement_usd" in ei.value.message
    assert "instruments.yaml" in ei.value.fix


def test_sample_task_loads_and_cross_checks() -> None:
    loaded = load_task(SAMPLE_TASK)
    assert loaded.task.id == "rail-3v3"
    assert loaded.card.id == "buck-5v-3v3"
    assert {i.case for i in loaded.task.instruments} == {"scpi-psu", "dmm"}
    assert {f.id for f in loaded.card.faults} == set(loaded.task.question.answer.values)


def test_task_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(TaskFormatError) as ei:
        load_task(tmp_path / "nope.yaml")
    assert ei.value.fix


def _load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _dump_yaml(path: Path, doc: dict) -> None:
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")


def test_unknown_top_level_key_in_task(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["bogus"] = 1
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "bogus" in ei.value.message
    assert ei.value.fix


def test_missing_required_key_in_task(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    del doc["seed"]
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "seed" in ei.value.message
    assert ei.value.fix


def test_wrong_arena_task_version(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["arena_task"] = 2
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "arena_task" in ei.value.message
    assert ei.value.fix


def test_wrong_arena_card_version(minimal_task: Path) -> None:
    card_path = minimal_task.parent / "card.yaml"
    doc = _load_yaml(card_path)
    doc["arena_card"] = 2
    _dump_yaml(card_path, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "arena_card" in ei.value.message
    assert ei.value.fix


def test_unknown_case(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["instruments"][0]["case"] = "not-a-real-case"
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "not-a-real-case" in ei.value.message
    assert "scpi-psu" in ei.value.fix  # names a real, known case


def test_drives_must_name_a_card_input(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["instruments"][0]["drives"] = "card.does_not_exist"
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "card.does_not_exist" in ei.value.message
    assert ei.value.fix


def test_probe_must_name_a_card_test_point(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    del doc["instruments"][0]["drives"]
    doc["instruments"][0]["probe"] = "card.not_a_test_point"
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "card.not_a_test_point" in ei.value.message
    assert ei.value.fix


def test_instrument_needs_exactly_one_of_drives_or_probe(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["instruments"][0]["probe"] = "card.tp1"  # now both drives and probe are set
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "exactly one" in ei.value.message
    assert ei.value.fix


def test_answer_values_must_equal_card_faults(minimal_task: Path) -> None:
    doc = _load_yaml(minimal_task)
    doc["question"]["answer"]["values"] = ["ok"]  # card also has "low_voltage"
    _dump_yaml(minimal_task, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "low_voltage" in ei.value.fix


def test_damage_threshold_without_source_is_rejected(minimal_task: Path) -> None:
    card_path = minimal_task.parent / "card.yaml"
    doc = _load_yaml(card_path)
    del doc["damage"][0]["source"]
    _dump_yaml(card_path, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "source" in ei.value.message
    assert ei.value.fix


def test_rail_test_points_must_be_unique(minimal_task: Path) -> None:
    card_path = minimal_task.parent / "card.yaml"
    doc = _load_yaml(card_path)
    doc["rails"]["r2"] = dict(doc["rails"]["r1"])  # same test_point as r1
    _dump_yaml(card_path, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "tp1" in ei.value.message
    assert ei.value.fix


def test_rail_with_tol_source_loads(minimal_task: Path) -> None:
    card_path = minimal_task.parent / "card.yaml"
    doc = _load_yaml(card_path)
    doc["rails"]["r1"]["tol_source"] = "test datasheet 3.2 output accuracy (fictional datasheet)"
    _dump_yaml(card_path, doc)
    loaded = load_task(minimal_task)
    rail = next(r for r in loaded.card.rails if r.name == "r1")
    assert rail.tol_source == "test datasheet 3.2 output accuracy (fictional datasheet)"


def test_rail_without_tol_source_loads_as_none(minimal_task: Path) -> None:
    loaded = load_task(minimal_task)
    rail = next(r for r in loaded.card.rails if r.name == "r1")
    assert rail.tol_source is None


@pytest.mark.parametrize("bad_value", ["", 123])
def test_rail_tol_source_must_be_non_empty_string(minimal_task: Path, bad_value: object) -> None:
    card_path = minimal_task.parent / "card.yaml"
    doc = _load_yaml(card_path)
    doc["rails"]["r1"]["tol_source"] = bad_value
    _dump_yaml(card_path, doc)
    with pytest.raises(TaskFormatError) as ei:
        load_task(minimal_task)
    assert "card.rails.r1.tol_source" in ei.value.message
    assert ei.value.fix


def test_packaged_cards_all_have_tol_source() -> None:
    card_paths = sorted(_CARDS_DIR.glob("*.yaml"))
    assert card_paths, "expected packaged cards under cards/"
    for card_path in card_paths:
        doc = yaml.safe_load(card_path.read_text(encoding="utf-8"))
        card = validate_card(doc)
        for rail in card.rails:
            assert rail.tol_source, f"{card_path.name}: rail {rail.name!r} has no tol_source"
