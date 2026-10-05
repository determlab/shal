"""Issue #313 Scope: "Generic card sim from yaml: rails, test points,
limits, list of possible faults (replaces a fixed card)." These tests build
`CardSim` from more than one `card.yaml` shape — an ad-hoc single-rail card
and an ad-hoc two-input, two-rail card, neither of them the package's
shipped buck-5v-3v3 example — to prove nothing in `card_sim.py` is hardcoded
to any one card. `test_damage.py` covers the Done-when damage-model
assertions specifically."""
from __future__ import annotations

import pytest

from shal_arena.card_sim import CardSim
from shal_arena.schema import validate_card

ONE_RAIL_CARD = validate_card({
    "arena_card": 1, "id": "one-rail", "description": "test",
    "inputs": {"vin": {"nominal_v": 5.0}},
    "rails": {"3v3": {"from": "vin", "nominal_v": 3.3, "tol_pct": 3,
                       "test_point": "tp_3v3", "min_input_v": 4.5}},
    "damage": [
        {"input": "vin", "above_v": 5.5, "effect": "protection",
         "source": "datasheet 7.3 OVP"},
        {"input": "vin", "above_v": 6.0, "effect": "destroyed",
         "source": "datasheet 6.1 abs max"},
    ],
    "faults": [{"id": "ok"}, {"id": "low_voltage", "rail": "3v3", "shift_v": -0.4}],
})

# never seen by card_sim.py before this test: two inputs, two rails, and a
# damage effect spelled "fuse" (the issue's other protection word) instead
# of "protection" — proves the sim generalizes past the shipped card's shape.
TWO_RAIL_CARD = validate_card({
    "arena_card": 1, "id": "dual-rail", "description": "test",
    "inputs": {"v12": {"nominal_v": 12.0}, "v5": {"nominal_v": 5.0}},
    "rails": {
        "1v8": {"from": "v12", "nominal_v": 1.8, "tol_pct": 5,
                "test_point": "tp_1v8", "min_input_v": 10.0},
        "3v3": {"from": "v5", "nominal_v": 3.3, "tol_pct": 5,
                "test_point": "tp_3v3", "min_input_v": 4.5},
    },
    "damage": [
        {"input": "v12", "above_v": 15.0, "effect": "destroyed",
         "source": "datasheet abs max 15V"},
        {"input": "v5", "above_v": 5.5, "effect": "fuse",
         "source": "datasheet fuse rating"},
    ],
    "faults": [{"id": "ok"}],
})


def _nominal(card, input_name: str) -> float:
    return next(i.nominal_v for i in card.inputs if i.name == input_name)


@pytest.mark.parametrize("card,input_name,test_point,nominal", [
    (ONE_RAIL_CARD, "vin", "tp_3v3", 3.3),
    (TWO_RAIL_CARD, "v12", "tp_1v8", 1.8),
    (TWO_RAIL_CARD, "v5", "tp_3v3", 3.3),
])
def test_an_undamaged_rail_reads_its_own_nominal_voltage(card, input_name, test_point,
                                                         nominal) -> None:
    sim = CardSim(card)
    sim.set_input_voltage(input_name, _nominal(card, input_name))
    assert sim.read_test_point(test_point) == pytest.approx(nominal)
    assert sim.state(input_name) == "ok"


def test_two_rail_card_tracks_each_input_independently() -> None:
    sim = CardSim(TWO_RAIL_CARD)
    sim.set_input_voltage("v12", 20.0)  # past v12's own 15.0V "destroyed" threshold
    assert sim.state("v12") == "damage"
    assert sim.state("v5") == "ok"  # untouched input stays ok
    assert sim.read_test_point("tp_3v3") == pytest.approx(3.3)  # its rail still regulates
    assert sim.read_test_point("tp_1v8") == 0.0  # v12's own rail is destroyed


def test_a_protection_effect_leaves_the_rail_still_regulating() -> None:
    sim = CardSim(ONE_RAIL_CARD)
    sim.set_input_voltage("vin", 5.7)  # past 5.5V OVP, under 6.0V destroy
    assert sim.state("vin") == "protection"
    assert sim.read_test_point("tp_3v3") == pytest.approx(3.3)  # "instrument alive"


def test_fuse_is_recognized_as_a_protection_word_too() -> None:
    sim = CardSim(TWO_RAIL_CARD)
    sim.set_input_voltage("v5", 6.0)  # past the "fuse" threshold only
    assert sim.state("v5") == "protection"


def test_unknown_input_raises_lookup_error() -> None:
    sim = CardSim(ONE_RAIL_CARD)
    with pytest.raises(LookupError):
        sim.set_input_voltage("nope", 5.0)
    with pytest.raises(LookupError):
        sim.state("nope")


def test_unknown_test_point_raises_lookup_error() -> None:
    sim = CardSim(ONE_RAIL_CARD)
    with pytest.raises(LookupError):
        sim.read_test_point("nope")


def test_state_starts_ok_for_every_input_before_any_voltage_is_applied() -> None:
    sim = CardSim(TWO_RAIL_CARD)
    assert sim.state("v12") == "ok"
    assert sim.state("v5") == "ok"
