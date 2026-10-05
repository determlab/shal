"""issue #316 Done-when: `pytest arena/tests/test_tasks.py -q` passes — the
three launch tasks (easy, medium, hard; `arena/src/shal_arena/tasks/<id>.yaml`
+ `cards/<id>.yaml`, packaged so `shal-arena run` works from a clean install)
each load, validate, and have a fault a measurement can tell apart from every
other fault on that card — plus the format rules the issue's body carries
forward from the CTO's task-file ruling (#310):

- no task file may carry `replacement_usd` (it lives only in the arena
  catalogue, `card_sim/catalogue/instruments.yaml`, keyed by `case`);
- every card's `damage` entries name a `source` (an undocumented limit is
  loaded but never counted — `CardSim.ignored_limits`);
- one of the three has a healthy card (its seed's fault is `ok`);
- a scripted run of each task — `start_run` -> `check_instrument_driver` on
  both addresses -> `take_measurement` on the probe -> `answer` — ends with a
  score file on disk, the same shape `test_scoring.py` already pins;
- the DMM fuse is a known runner quirk (`runner.take_measurement`: any read
  through a `case: dmm` instrument prices the card's current supply draw
  against `catalogue()["dmm"].fuse_a`, whether or not the instrument is
  wired for current) — none of these three tasks wires the DMM for current,
  and a scripted run that never drives the card past its own damage
  threshold never trips it either, so no task's scripted run should ever
  come back with `fuse: "protection"`.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shal_arena.loader import load_task
from shal_arena.runner import (
    answer,
    check_instrument_driver,
    pick_fault,
    start_run,
    take_measurement,
)
from shal_arena.schema import TASK_VERSION, validate_task
from shal_arena.score import validate_score
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_DRIVER

_TASKS_DIR = Path(__file__).resolve().parents[1] / "src" / "shal_arena" / "tasks"
_CARDS_DIR = Path(__file__).resolve().parents[1] / "src" / "shal_arena" / "cards"
_LEVELS = ("easy", "medium", "hard")
TASK_PATHS = {level: _TASKS_DIR / f"{level}.yaml" for level in _LEVELS}


# --------------------------------------------------------------------------- #
# each task loads, validates, and its fault is detectable by measurement
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_task_file_exists_at_the_packaged_agent_path(level: str) -> None:
    # the Agent path line in the issue: `shal-arena run
    # arena/src/shal_arena/tasks/<level>.yaml --json` must find a real file.
    assert TASK_PATHS[level].is_file(), TASK_PATHS[level]


@pytest.mark.parametrize("level", _LEVELS)
def test_task_loads_and_validates(level: str) -> None:
    loaded = load_task(TASK_PATHS[level])
    assert loaded.task.level == level
    assert loaded.task.id
    assert loaded.card.id


@pytest.mark.parametrize("level", _LEVELS)
def test_task_and_card_are_the_packaged_pair_shal_arena_run_uses(level: str) -> None:
    # Scope: "tasks/<id>.yaml and cards/<id>.yaml ... packaged, so `run`
    # works from a clean install; not arena/tasks/". The card path a task
    # names must itself resolve under the packaged `cards/` directory, not
    # some ad hoc location outside the installed package.
    loaded = load_task(TASK_PATHS[level])
    assert loaded.card_path.parent.name == "cards"
    assert loaded.card_path.parent.parent == TASK_PATHS[level].parent.parent


def _seeds_realizing(card, fault_id: str, *, count: int, search_limit: int = 2000) -> list[int]:
    """The first ``count`` seeds (out of ``search_limit`` tried, from 0) for
    which `pick_fault` realizes ``fault_id`` on ``card``."""
    seeds: list[int] = []
    for seed in range(search_limit):
        if pick_fault(card, seed) == fault_id:
            seeds.append(seed)
            if len(seeds) >= count:
                break
    return seeds


_TASK_FAULT_PAIRS = [
    (level, fault_id)
    for level in _LEVELS
    for fault_id in [f.id for f in load_task(TASK_PATHS[level]).card.faults]
]


@pytest.mark.parametrize("level,fault_id", _TASK_FAULT_PAIRS)
def test_every_fault_is_detectable_by_measurement(level: str, fault_id: str,
                                                   tmp_path: Path) -> None:
    """Every fault id a card can realize (not only the one picked by the
    task's own default seed) must be distinguishable, by a probing
    measurement, from a healthy reading: 'open' makes the instrument
    unreachable (a `MeasurementFailed`, itself a detectable signal distinct
    from any reading at all); a value-class fault ('low_voltage' /
    'high_voltage' / 'noise') must move the reading off the rail's nominal
    voltage; 'ok' must read at nominal.

    'noise' is checked across (at least) 10 seeds, not one: its realized
    ripple is rescaled 0.7-1.3x per seed (`fault.py` `_NOISE_SCALE_RANGE`,
    `realized_fault`), so a single seed proves only that one scaled ripple,
    not the fault class — e.g. 'hard' (ripple_vpp 0.15) can realize anywhere
    from 0.105 to 0.195 Vpp."""
    from shal_arena.errors import MeasurementFailed

    loaded = load_task(TASK_PATHS[level])
    task, card = loaded.task, loaded.card
    probe_instrument = next(i for i in task.instruments if i.probe is not None)
    rail = next(r for r in card.rails if f"card.{r.test_point}" == probe_instrument.probe)

    seed_count = 10 if fault_id == "noise" else 1
    seeds = _seeds_realizing(card, fault_id, count=seed_count)
    assert len(seeds) >= seed_count, (
        f"{level}: could not find {seed_count} seed(s) realizing fault {fault_id!r}")

    for seed in seeds:
        state_dir = tmp_path / f"state-{seed}"
        result = start_run(TASK_PATHS[level], seed=seed, state_dir=state_dir)
        run_id = result["run_id"]
        try:
            measured = take_measurement(run_id, str(probe_instrument.address),
                                        PASSING_DMM_DRIVER, state_dir=state_dir)
        except MeasurementFailed:
            assert fault_id == "open", (
                f"{level}: seed {seed} measurement failed for fault {fault_id!r}, "
                "not 'open'")
            continue

        reading = measured["reading"]
        if fault_id == "ok":
            assert reading == pytest.approx(rail.nominal_v, abs=1e-6)
        else:
            assert reading != pytest.approx(rail.nominal_v, abs=1e-6), (
                f"{level}: seed {seed}, fault {fault_id!r} left the reading at "
                f"nominal ({rail.nominal_v}) — not detectable by measurement")


# --------------------------------------------------------------------------- #
# card file names match card ids; every task's card path resolves (#334 step 2)
# --------------------------------------------------------------------------- #

_ALL_CARD_PATHS = sorted(_CARDS_DIR.glob("*.yaml"))
_ALL_TASK_PATHS = sorted(_TASKS_DIR.glob("*.yaml"))


@pytest.mark.parametrize("card_path", _ALL_CARD_PATHS, ids=lambda p: p.stem)
def test_card_file_stem_equals_card_id(card_path: Path) -> None:
    doc = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    assert card_path.stem == doc["id"], (
        f"{card_path.name}: file stem does not match card id {doc['id']!r}")


@pytest.mark.parametrize("task_path", _ALL_TASK_PATHS, ids=lambda p: p.stem)
def test_task_card_path_resolves_to_an_existing_file(task_path: Path) -> None:
    doc = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    card_path = (task_path.parent / doc["card"]).resolve()
    assert card_path.is_file(), (
        f"{task_path.name}: card: {doc['card']!r} does not resolve to a file ({card_path})")


# --------------------------------------------------------------------------- #
# every card is marked fictional (#334 step 3)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("card_path", _ALL_CARD_PATHS, ids=lambda p: p.stem)
def test_card_description_says_fictional(card_path: Path) -> None:
    doc = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    assert doc["description"].startswith("Fictional card"), (
        f"{card_path.name}: description does not start with 'Fictional card' "
        f"({doc['description']!r})")


@pytest.mark.parametrize("card_path", _ALL_CARD_PATHS, ids=lambda p: p.stem)
def test_card_damage_sources_say_fictional(card_path: Path) -> None:
    doc = yaml.safe_load(card_path.read_text(encoding="utf-8"))
    damage = doc.get("damage") or []
    assert damage, f"{card_path.name}: card has no damage entries at all"
    for d in damage:
        assert "fictional" in d["source"].lower(), (
            f"{card_path.name}: damage source for input {d['input']!r} does not say "
            f"'fictional' ({d['source']!r})")


# --------------------------------------------------------------------------- #
# format rules carried forward from the CTO's task/card ruling (#310)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_no_replacement_usd_in_any_task_file(level: str) -> None:
    doc = yaml.safe_load(TASK_PATHS[level].read_text(encoding="utf-8"))
    assert "replacement_usd" not in TASK_PATHS[level].read_text(encoding="utf-8")
    for instrument in doc["instruments"]:
        assert "replacement_usd" not in instrument


@pytest.mark.parametrize("level", _LEVELS)
def test_schema_rejects_replacement_usd_added_to_a_task_file(level: str) -> None:
    doc = yaml.safe_load(TASK_PATHS[level].read_text(encoding="utf-8"))
    doc["instruments"][0]["replacement_usd"] = 1200
    from shal_arena.errors import TaskFormatError

    with pytest.raises(TaskFormatError) as ei:
        validate_task(doc)
    assert "replacement_usd" in ei.value.message
    assert "card_sim/catalogue/instruments.yaml" in ei.value.fix


@pytest.mark.parametrize("level", _LEVELS)
def test_every_damage_line_has_a_source(level: str) -> None:
    loaded = load_task(TASK_PATHS[level])
    assert loaded.card.damage, f"{level}: card has no damage entries at all"
    for d in loaded.card.damage:
        assert d.source and d.source.strip(), (
            f"{level}: card.damage entry for input {d.input!r} has no source")


@pytest.mark.parametrize("level", _LEVELS)
def test_task_file_is_version_1(level: str) -> None:
    doc = yaml.safe_load(TASK_PATHS[level].read_text(encoding="utf-8"))
    assert doc["arena_task"] == TASK_VERSION


def test_one_task_has_a_healthy_card_and_its_seed_picks_it() -> None:
    """Scope: 'One task has a healthy card: its card has the ok fault and
    its seed picks it. A test asserts the seed's pick.' -- exactly one of
    the three (by design, 'easy': the player must confirm nothing is wrong,
    rather than name a fault)."""
    healthy = []
    for name, path in TASK_PATHS.items():
        loaded = load_task(path)
        assert "ok" in {f.id for f in loaded.card.faults}, f"{name}: card has no 'ok' fault"
        if pick_fault(loaded.card, loaded.task.seed) == "ok":
            healthy.append(name)
    assert healthy == ["easy"], (
        f"expected exactly 'easy' to be the healthy task, got {healthy}")


# --------------------------------------------------------------------------- #
# a scripted run of each task ends with a score file
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_scripted_run_ends_with_a_score_file(level: str, tmp_path: Path) -> None:
    from shal_arena.errors import MeasurementFailed

    state_dir = tmp_path / "state"
    loaded = load_task(TASK_PATHS[level])
    task = loaded.task
    psu = next(i for i in task.instruments if i.drives is not None)
    dmm = next(i for i in task.instruments if i.probe is not None)

    result = start_run(TASK_PATHS[level], state_dir=state_dir)
    run_id = result["run_id"]

    psu_check = check_instrument_driver(run_id, str(psu.address), PASSING_DRIVER,
                                        state_dir=state_dir)
    dmm_check = check_instrument_driver(run_id, str(dmm.address), PASSING_DMM_DRIVER,
                                        state_dir=state_dir)
    assert psu_check["passed"] is True
    assert dmm_check["passed"] is True

    fault_id = pick_fault(loaded.card, task.seed)
    try:
        take_measurement(run_id, str(dmm.address), PASSING_DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:
        pass  # 'open': the attempt itself is a live error, counted as measured regardless

    out = answer(run_id, fault_id, state_dir=state_dir)
    assert out["correct"] is True
    assert out["disqualified"] is False

    store = RunStore(state_dir)
    score_path = store.score_path(run_id)
    assert score_path.is_file(), f"{level}: no score file written at {score_path}"
    validate_score(out["score"])


# --------------------------------------------------------------------------- #
# DMM fuse: a voltage-mode probe must never score as fuse damage
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("level", _LEVELS)
def test_no_task_wires_the_dmm_for_current(level: str) -> None:
    loaded = load_task(TASK_PATHS[level])
    dmm_instruments = [i for i in loaded.task.instruments if i.case == "dmm"]
    assert dmm_instruments, f"{level}: task has no dmm instrument"
    for inst in dmm_instruments:
        assert inst.probe is not None and inst.drives is None, (
            f"{level}: a dmm instrument must probe a test point, never drive an input")


@pytest.mark.parametrize("level", _LEVELS)
def test_scripted_run_never_scores_fuse_damage_from_a_voltage_mode_read(
        level: str, tmp_path: Path) -> None:
    """known runner behaviour (`runner.take_measurement`): the fuse check
    runs on every read through a `case: dmm` instrument, keyed only on the
    instrument's case having a `fuse_a`, not on which op was actually
    called. A task whose DMM only ever probes a voltage test point (as all
    three of these do) must still come back with `fuse: 'ok'`, never
    `'protection'`, for a scripted run that stays inside the card's own
    documented limits."""
    from shal_arena.errors import MeasurementFailed

    state_dir = tmp_path / "state"
    loaded = load_task(TASK_PATHS[level])
    task = loaded.task
    dmm = next(i for i in task.instruments if i.probe is not None)

    run_id = start_run(TASK_PATHS[level], state_dir=state_dir)["run_id"]
    try:
        out = take_measurement(run_id, str(dmm.address), PASSING_DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:
        return  # 'open': unreachable, nothing to score as fuse damage either
    assert out.get("fuse") != "protection", (
        f"{level}: a voltage-mode read tripped the DMM fuse — {out}")


@pytest.mark.parametrize("level", _LEVELS)
def test_every_fault_realization_leaves_the_fuse_unblown(level: str, tmp_path: Path) -> None:
    """Broader than the task's own default seed: across every fault this
    card can realize, a scripted run that only ever probes voltage must
    never come back fuse-blown — the scenario the issue calls out ('the
    fuse trips on any read after overvoltage, not only in current mode')
    never applies here because none of these cards' damage thresholds are
    ever crossed by a scripted run that drives the card at its own nominal
    input voltage."""
    from shal_arena.errors import MeasurementFailed

    loaded = load_task(TASK_PATHS[level])
    task, card = loaded.task, loaded.card
    psu = next(i for i in task.instruments if i.drives is not None)
    dmm = next(i for i in task.instruments if i.probe is not None)
    nominal_in = next(inp.nominal_v for inp in card.inputs if f"card.{inp.name}" == psu.drives)

    seeds = []
    seen_faults: set[str] = set()
    for seed in range(200):
        fault_id = pick_fault(card, seed)
        if fault_id not in seen_faults:
            seen_faults.add(fault_id)
            seeds.append(seed)
    assert seen_faults == {f.id for f in card.faults}, (
        f"{level}: 200 seeds did not realize every fault id on this card: "
        f"missing {sorted({f.id for f in card.faults} - seen_faults)}")

    for i, seed in enumerate(seeds):
        state_dir = tmp_path / f"state-{i}"
        run_id = start_run(TASK_PATHS[level], seed=seed, state_dir=state_dir)["run_id"]
        from shal_arena.runner import drive_input
        drive_input(run_id, str(psu.address), nominal_in, state_dir=state_dir)
        try:
            out = take_measurement(run_id, str(dmm.address), PASSING_DMM_DRIVER,
                                   state_dir=state_dir)
        except MeasurementFailed:
            continue
        assert out.get("fuse") != "protection", (
            f"{level}: seed {seed} (fault {pick_fault(card, seed)!r}) tripped the DMM "
            f"fuse on a voltage-mode read — {out}")
