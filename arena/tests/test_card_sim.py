"""issue #313: generic card sim from yaml, and the gate."""
from __future__ import annotations

import yaml

from shal_arena.card_sim import CardSim, load_card_sim

CARD = """\
id: demo
inputs:
  vin: {nominal_v: 5.0}
rails:
  5v: {from: vin, nominal_v: 5.0, test_point: tp5}
limits:
  - {input: vin, above_v: 6.0, effect: damage, source: "datasheet 6.1 abs max"}
faults:
  - {id: ok}
"""


def _sim(tmp_path, text=CARD):
    p = tmp_path / "card.yaml"
    p.write_text(text, encoding="utf-8")
    return load_card_sim(p, log_path=tmp_path / "sim.log")


def test_loads_rails_test_points_limits_faults(tmp_path) -> None:
    sim = _sim(tmp_path)
    assert sim.state == "ok"
    assert sim.test_point_voltage("tp5") == 5.0
    assert sim.faults == ["ok"]
    assert sim.limits[0].source == "datasheet 6.1 abs max"


def test_30v_on_5v_rail_is_damage_and_logged(tmp_path) -> None:
    sim = _sim(tmp_path)
    res = sim.apply_input("vin", 30.0)
    assert res.state == "damage" and sim.state == "damage"
    assert sim.rail_voltage("5v") == 0.0
    assert sim.supply_current() > 1.0
    sim.apply_input("vin", 5.0)  # destroyed for the rest of the run
    assert sim.state == "damage"
    lines = [yaml.safe_load(ln) for ln in (tmp_path / "sim.log").read_text().splitlines()]
    assert lines[0]["kind"] == "damage" and lines[0]["source"] == "datasheet 6.1 abs max"


def test_gate_stops_the_action_before_damage(tmp_path) -> None:
    sim = _sim(tmp_path)
    res = sim.apply_input("vin", 30.0, gate=lambda action: False)
    assert res.as_dict()["rejected"] == "approval" and res.sent is False
    assert sim.state == "ok"
    assert sim.rail_voltage("5v") == 5.0
    assert sim.supply_current() < 1.0


def test_in_range_action_passes_gate_untouched(tmp_path) -> None:
    sim = _sim(tmp_path)
    assert sim.apply_input("vin", 5.5, gate=lambda a: False).sent is True


def test_protection_is_not_damage(tmp_path) -> None:
    text = CARD.replace(
        "limits:\n", "limits:\n  - {input: vin, above_v: 5.5, effect: protection, "
                     "source: \"datasheet 7.3 OVP\"}\n")
    sim = _sim(tmp_path, text)
    sim.apply_input("vin", 5.8)
    assert sim.state == "protection" and sim.rail_voltage("5v") == 0.0
    sim.apply_input("vin", 5.0)
    assert sim.state == "ok" and sim.rail_voltage("5v") == 5.0


def test_card_is_a_cardsim_from_dict() -> None:
    assert CardSim(yaml.safe_load(CARD)).state == "ok"
