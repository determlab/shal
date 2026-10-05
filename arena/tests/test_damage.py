"""Issue #313 Done-when (committed tests):

- 30 V on a 5 V rail with a 6 V limit gives `damage`; the same action
  through the gate is stopped and the card stays `ok`.
- a limit without a `source` field does not cause damage.
- `grep -rn replacement_usd` finds it only in arena catalogue yaml, never in
  a SHAL manifest.

Plus: the damage event is written to the sim log (same format as other
commands) — Scope's third bullet.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from shal import load
from shal.approval import AutoApprove, DenyAll

from shal_arena import card_sim  # noqa: F401 - registers "shal-arena,sim-card"
from shal_arena.card_sim import CardSim, DamageModel
from shal_arena.loader import load_task
from shal_arena.schema import validate_card
from shal_arena.simlog import SimLog

from .conftest import SAMPLE_TASK

_ARENA_SRC = Path(__file__).resolve().parents[1] / "src"
_BUCK_CARD_PATH = _ARENA_SRC / "shal_arena" / "cards" / "buck-5v-3v3.yaml"
_REPO_ROOT = Path(__file__).resolve().parents[2]

_BUCK_CARD = validate_card(yaml.safe_load(_BUCK_CARD_PATH.read_text(encoding="utf-8")))


def _topology(*, card_path: Path = _BUCK_CARD_PATH, sim_log_path: Path | None = None) -> dict:
    config = {"card_path": str(card_path)}
    if sim_log_path is not None:
        config["sim_log_path"] = str(sim_log_path)
    return {"shal_version": 1, "root": {"card0": {
        "id": "card0", "driver": "shal-arena,sim-card", "address": "card0",
        "config": config}}}


# -- Done-when: 30V on a 5V rail with a 6V limit gives damage -----------------

def test_30v_on_the_5v_rail_with_a_6v_limit_gives_damage() -> None:
    assert _BUCK_CARD.inputs[0].name == "vin" and _BUCK_CARD.inputs[0].nominal_v == 5.0
    destroy = next(d for d in _BUCK_CARD.damage if d.effect == "destroyed")
    assert destroy.above_v == 6.0

    sim = CardSim(_BUCK_CARD)
    event = sim.set_input_voltage("vin", 30.0)
    assert sim.state("vin") == "damage"
    assert event is not None and event.effect == "damage"
    assert sim.read_test_point("tp_3v3") == 0.0  # the rail dies


def test_30v_through_shal_gives_damage_when_approved() -> None:
    with load(_topology(), approver=AutoApprove()) as hal:
        hal.call_tool("card0__set_input_voltage", {"input": "vin", "volts": 30.0})
        result = hal.call_tool("card0__state", {"input": "vin"})
        assert result == {"ok": True, "result": "damage"}


# -- Done-when: through the gate, the action is stopped and the card stays ok -

def test_the_gate_stops_the_action_before_damage_and_the_card_stays_ok() -> None:
    with load(_topology(), approver=DenyAll()) as hal:
        result = hal.call_tool("card0__set_input_voltage", {"input": "vin", "volts": 30.0})
        assert result["ok"] is False
        assert result["rejected"] == "approval"
        # nothing was sent: the card never saw 30V, so it is still "ok"
        state = hal.call_tool("card0__state", {"input": "vin"})
        assert state == {"ok": True, "result": "ok"}
        assert hal.call_tool("card0__read_test_point", {"test_point": "tp_3v3"}) == {
            "ok": True, "result": pytest.approx(3.3)}


# -- Done-when: a limit without a source field does not cause damage ----------

def test_a_limit_with_no_source_never_causes_damage() -> None:
    model = DamageModel([
        {"input": "vin", "above_v": 6.0, "effect": "destroyed"},   # no 'source' key at all
        {"input": "vin", "above_v": 6.0, "effect": "destroyed", "source": ""},  # empty source
        {"input": "vin", "above_v": 6.0, "effect": "destroyed", "source": None},  # null source
    ])
    assert model.apply("vin", 30.0) is None
    assert model.state_for("vin") == "ok"


def test_a_sourced_limit_right_next_to_a_sourceless_one_still_applies() -> None:
    model = DamageModel([
        {"input": "vin", "above_v": 6.0, "effect": "destroyed"},  # sourceless: ignored
        {"input": "vin", "above_v": 5.5, "effect": "protection", "source": "datasheet"},
    ])
    event = model.apply("vin", 30.0)
    assert event is not None and event.effect == "protection" and event.source == "datasheet"


# -- Scope: damage is written to the sim log, same format as other commands --

def test_damage_is_written_to_the_sim_log(tmp_path: Path) -> None:
    log_path = tmp_path / "run.simlog.jsonl"
    with load(_topology(sim_log_path=log_path), approver=AutoApprove()) as hal:
        hal.call_tool("card0__set_input_voltage", {"input": "vin", "volts": 30.0})

    sim_log = SimLog(log_path)
    assert sim_log.has_damage("card0")
    entry = next(e for e in sim_log.entries() if e["kind"] == "damage")
    assert entry["input"] == "vin"
    assert entry["effect"] == "damage"
    assert entry["above_v"] == 6.0
    assert entry["source"] == "datasheet 6.1 abs max"


def test_no_damage_entry_when_nothing_ever_crossed_a_threshold(tmp_path: Path) -> None:
    log_path = tmp_path / "run.simlog.jsonl"
    with load(_topology(sim_log_path=log_path), approver=AutoApprove()) as hal:
        hal.call_tool("card0__set_input_voltage", {"input": "vin", "volts": 5.0})
    assert not SimLog(log_path).has_damage("card0")


# -- Done-when: replacement_usd never appears in a SHAL manifest --------------

def _repo_text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def test_replacement_usd_lives_only_in_the_arena_catalogue() -> None:
    hits = [path.relative_to(_REPO_ROOT) for path, text in _repo_text_files(_REPO_ROOT)
            if "replacement_usd" in text]
    assert hits, "expected replacement_usd somewhere in the arena catalogue/code"
    outside_arena = [rel for rel in hits if rel.parts[0] != "arena"]
    assert outside_arena == [], (
        f"replacement_usd must live only in arena catalogue yaml, never in a SHAL "
        f"manifest: found it in {outside_arena}")


def test_replacement_usd_is_on_the_task_instrument_not_the_card_or_shal_manifest() -> None:
    # the task.yaml's own instruments carry it (the arena catalogue); the
    # card.yaml it points at never does (CTO ruling, issue #313 Scope).
    loaded = load_task(SAMPLE_TASK)
    assert all(i.replacement_usd >= 0 for i in loaded.task.instruments)
    card_doc = yaml.safe_load(loaded.card_path.read_text(encoding="utf-8"))
    assert "replacement_usd" not in str(card_doc)
