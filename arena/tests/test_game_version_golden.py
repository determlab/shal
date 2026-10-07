"""CTO review on #480: `score.GAME_VERSION` names the game's rules -- which
fault a seed realizes, what each probe reads, how the right answer scores.
This test plays every packaged task for seeds 0-29 the same fixed way (drive
the card's nominal input, measure every probe once, answer the realized
fault), hashes that table, and pins the hash per `GAME_VERSION`. Changing
the rules without bumping the version fails here; bumping it means adding
the new version's hash below (the failure message prints it).

It also renders a run made on main under game 0.4.0 (the `open` fault then
raised `MeasurementFailed`) with today's code, so old stored runs keep
replaying. The fixture's two `*_path` fields were rewritten repo-relative
(they held the author's local paths) and `record_sha256` recomputed for
that rewrite; every other byte is as main wrote it.
"""
from __future__ import annotations

import hashlib
import json
from importlib import resources as importlib_resources
from pathlib import Path

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.score import GAME_VERSION

from .conftest import FIXTURES, PASSING_DMM_DRIVER, PASSING_TEMP_DRIVER

#: sha256 of `_rules_table()` per game version
GOLDEN = {
    "0.4.1": "85ae276276b0a8cae924b04b4918756b2f0fb578609e7330b15098495aff1be0",
}

_SEEDS = range(30)
_DRIVERS = {"dmm": PASSING_DMM_DRIVER, "sht31": PASSING_TEMP_DRIVER}
_TASKS = sorted(Path(str(importlib_resources.files("shal_arena") / "tasks")).glob("*.yaml"))
_MAIN_040_OPEN_RUN = FIXTURES / "runs" / "main-0.4.0-open"
# the score fields that carry rules, not wall-clock time or file hashes
_SCORE_KEYS = ("fault_type", "faults_total", "faults_caught", "false_fails",
               "error_fail_correct", "turns", "gate_stops", "schema_version")


def _play(task_path: Path, seed: int, state_dir: Path) -> dict:
    loaded = load_task(str(task_path))
    task, card = loaded.task, loaded.card
    run_id = start_run(str(task_path), seed=seed, state_dir=state_dir)["run_id"]
    psu = next(i for i in task.instruments if i.case == "scpi-psu")
    vin = next(i for i in card.inputs if i.name == psu.drives.split(".", 1)[1])
    drive_input(run_id, str(psu.address), vin.nominal_v, state_dir=state_dir)
    readings = {}
    for probe in (i for i in task.instruments if i.probe is not None):
        reading = take_measurement(run_id, str(probe.address), _DRIVERS[probe.case],
                                   state_dir=state_dir)["reading"]
        readings[str(probe.address)] = round(reading, 6)
    fault_id = fault_mod.realized_fault(card, seed).fault_id
    out = answer(run_id, fault_id, state_dir=state_dir)
    return {"task": task.id, "seed": seed, "fault_id": fault_id, "readings": readings,
            "correct": out["correct"], "disqualified": out["disqualified"],
            "score": {k: out["score"][k] for k in _SCORE_KEYS}}


def _rules_table(state_dir: Path) -> list[dict]:
    return [_play(task_path, seed, state_dir / task_path.stem / str(seed))
            for task_path in _TASKS for seed in _SEEDS]


def test_game_rules_match_the_pinned_hash_for_this_game_version(tmp_path):
    table = _rules_table(tmp_path)
    assert all(row["correct"] and not row["disqualified"] for row in table)
    digest = hashlib.sha256(json.dumps(table, sort_keys=True).encode("utf-8")).hexdigest()
    assert GAME_VERSION in GOLDEN, (
        f"GAME_VERSION {GAME_VERSION!r} has no pinned hash; add "
        f"{GAME_VERSION!r}: {digest!r} to GOLDEN")
    assert digest == GOLDEN[GAME_VERSION], (
        f"the game's rules changed under GAME_VERSION {GAME_VERSION!r}: bump "
        f"score.GAME_VERSION and pin the new hash {digest!r} for it")


def test_a_run_made_under_game_0_4_0_still_renders(tmp_path):
    """The main-made `open` run (0.4.0: the read raised, error_fail_correct 1)
    renders as a result card under the current code, from its stored score."""
    import shutil

    from shal_arena.replay.card import build_result_card

    state_dir = tmp_path / "state"
    shutil.copytree(_MAIN_040_OPEN_RUN, state_dir)
    run_id = next(state_dir.glob("*.score.json")).name.removesuffix(".score.json")
    score = json.loads((state_dir / f"{run_id}.score.json").read_text(encoding="utf-8"))
    assert score["game_version"] == "0.4.0" and score["error_fail_correct"] == 1

    html = build_result_card(run_id, state_dir=state_dir)
    assert "1 of 1 faults caught." in html
    assert "seed 0" in html
