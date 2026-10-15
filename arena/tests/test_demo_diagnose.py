"""issue #495: `shal_arena.demo`'s scripted player names `probe` on a
`broken_probe` seed (the DMM reads ~0 V but the card draws its normal supply
current), still names `open` when the card draws nothing, and its result card
carries the #478 result line. Real runs of the packaged relay-rail task."""
from __future__ import annotations

import html as html_mod
from pathlib import Path

import pytest

from shal_arena import demo
from shal_arena.loader import load_task
from shal_arena.replay.card import build_result_card

from .conftest import RELAY_RAIL_TASK
from .test_broken_probe_fault import BROKEN_PROBE_SEEDS, OK_SEED, OPEN_SEED


def _play(tmp_path: Path, seed: int) -> tuple[dict, dict]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dmm_driver.py").write_text(demo._DMM_DRIVER_SOURCE, encoding="utf-8")
    ctx = {"state_dir": tmp_path / "state", "dmm_driver": tmp_path / "dmm_driver.py",
           "arena_runs": {}}
    return demo._run_arena_task(ctx, "relay-rail", seed=seed), ctx


@pytest.mark.parametrize("seed", BROKEN_PROBE_SEEDS)
def test_demo_answers_probe_on_a_broken_probe_seed_and_is_correct(seed, tmp_path):
    result, _ = _play(tmp_path, seed)
    assert result["given"] == "probe"
    assert result["correct"] is True and result["disqualified"] is False
    assert demo.check_arena_task(result)


def test_other_seeds_keep_their_answers(tmp_path):
    assert _play(tmp_path / "open", OPEN_SEED)[0]["given"] == "open"
    assert _play(tmp_path / "ok", OK_SEED)[0]["given"] == "ok"


def test_diagnose_needs_the_current_to_say_probe():
    rail = load_task(str(RELAY_RAIL_TASK)).card.rails[0]
    allowed = {"ok", "open", "probe"}
    assert demo._diagnose(rail, 0.01, allowed, current_a=0.05) == "probe"
    assert demo._diagnose(rail, 0.01, allowed, current_a=0.0) == "open"
    assert demo._diagnose(rail, 0.01, allowed) == "open"
    assert demo._diagnose(rail, 0.01, {"ok", "open"}, current_a=0.05) == "open"


def test_the_demo_card_carries_the_correct_result_line(tmp_path):
    _, ctx = _play(tmp_path, BROKEN_PROBE_SEEDS[0])
    run_id, state_dir = ctx["arena_runs"]["relay-rail"]
    html = build_result_card(run_id, state_dir=state_dir)
    assert "0 of 0" not in html and "No fault was injected." in html
    assert "Measured: tp_3v3 reads " in html
    assert html_mod.escape(
        "The agent's answer: probe. Correct: the card is fine, the probe is broken.") in html
    assert "This is a false fail" not in html
