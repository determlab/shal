"""issue #312 Done-when: ``pytest arena/tests/test_fault_seed.py -q`` passes —
same seed gives the same fault and noise, a different seed differs."""
from __future__ import annotations

from shal_arena.fault import realized_fault
from shal_arena.loader import load_task
from shal_arena.runner import pick_fault

from .conftest import SAMPLE_TASK


def _card():
    return load_task(SAMPLE_TASK).card


def test_same_seed_gives_the_same_fault_and_noise() -> None:
    card = _card()
    a = realized_fault(card, 4172)
    b = realized_fault(card, 4172)
    assert a == b


def test_a_different_seed_can_give_a_different_fault() -> None:
    card = _card()
    results = {realized_fault(card, seed).fault_id for seed in range(100)}
    assert len(results) > 1, "100 different seeds should not all pick the same fault"


def test_a_different_seed_gives_different_noise_for_the_same_fault() -> None:
    card = _card()
    ripples = {
        round(realized_fault(card, seed).extra["ripple_vpp"], 9)
        for seed in range(300)
        if realized_fault(card, seed).fault_id == "noise"
    }
    assert len(ripples) > 1, (
        "the realized 'noise' fault's ripple should vary across seeds, not just its id")


def test_pick_fault_matches_realized_fault_id_for_every_seed() -> None:
    card = _card()
    for seed in (0, 1, 2, 3, 4172, 99999):
        assert pick_fault(card, seed) == realized_fault(card, seed).fault_id
