"""issue #477: the `open` fault is an open circuit on the card, not an
unplugged instrument. The DMM still answers and reads about 0 V; both
scripted diagnoses name `open` from that reading. A real unplug
(`SHAL_SIM_UNPLUG`) is a broken link to the bench: `error`, cause
`transport`, never `open` and never a card fault.
"""
from __future__ import annotations

import importlib.util
import runpy
import sys
from importlib import resources as importlib_resources
from pathlib import Path
from types import ModuleType

import pytest
from shal.errors import HopError
from shal.hal import load

from shal_arena import demo
from shal_arena import fault as fault_mod
from shal_arena.cases import resolve_case
from shal_arena.errors import MeasurementFailed
from shal_arena.loader import load_task
from shal_arena.runner import answer, drive_input, start_run, take_measurement

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS_DIR = Path(str(importlib_resources.files("shal_arena") / "tasks"))
PACKAGED_TASKS = sorted(TASKS_DIR.glob("*.yaml"))
#: the seeds swept per task -- the same 0..99 window the other fault tests use,
#: plus each task's own default seed
_SEEDS = range(100)
#: the arena DMM harness's own node id (adk/dmm/harness/topology.yaml)
_DMM_NODE_ID = "unit"


def _load_story() -> ModuleType:
    if "story" in sys.modules:
        return sys.modules["story"]
    spec = importlib.util.spec_from_file_location("story", REPO_ROOT / "dev" / "e2e" / "story.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["story"] = mod
    spec.loader.exec_module(mod)
    return mod


story = _load_story()


def _parts(task_path: Path):
    loaded = load_task(str(task_path))
    task, card = loaded.task, loaded.card
    drives = next(i for i in task.instruments if i.drives is not None)
    vin = next(i for i in card.inputs if i.name == drives.drives.split(".", 1)[1])
    return loaded, drives, vin


def _open_seeds(task_path: Path) -> list[int]:
    loaded = load_task(str(task_path))
    seeds = sorted({*_SEEDS, loaded.task.seed})
    return [s for s in seeds
            if fault_mod.realized_fault(loaded.card, s).fault_id == "open"]


def _open_rail(loaded, seed: int):
    return fault_mod.rail_for_fault(loaded.card, fault_mod.realized_fault(loaded.card, seed))


def _probe_for(loaded, rail):
    return next(i for i in loaded.task.instruments
                if i.probe == f"card.{rail.test_point}")


def _measure_open(task_path: Path, seed: int, state_dir: Path) -> tuple[float, object]:
    loaded, drives, vin = _parts(task_path)
    rail = _open_rail(loaded, seed)
    probe = _probe_for(loaded, rail)
    run_id = start_run(str(task_path), seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, str(drives.address), vin.nominal_v, state_dir=state_dir)
    reading = take_measurement(run_id, str(probe.address), PASSING_DMM_DRIVER,
                               state_dir=state_dir)["reading"]
    return reading, rail


def test_every_packaged_task_has_open_seeds():
    for task_path in PACKAGED_TASKS:
        assert _open_seeds(task_path), f"{task_path.name}: no seed in 0..99 realizes open"


@pytest.mark.parametrize("task_path", PACKAGED_TASKS, ids=lambda p: p.stem)
def test_open_reads_near_zero_and_both_diagnoses_name_open(task_path, tmp_path):
    """DoD 1 + 2: for every open seed, `measure` on the probe returns a
    reading below 5% of nominal (no HopError, no MeasurementFailed), and
    both `_diagnose` functions name `open` from that reading."""
    loaded, _drives, _vin = _parts(task_path)
    allowed = set(loaded.task.question.answer.values)
    for seed in _open_seeds(task_path):
        reading, rail = _measure_open(task_path, seed, tmp_path / str(seed))
        assert isinstance(reading, float)
        assert abs(reading) < 0.05 * rail.nominal_v, (task_path.name, seed, reading)
        assert demo._diagnose(rail, reading, allowed) == "open", (task_path.name, seed)
        assert story._diagnose(rail.nominal_v, rail.tol_pct, [reading] * story._N_READS,
                               allowed) == "open", (task_path.name, seed)


@pytest.mark.parametrize("task_path", PACKAGED_TASKS, ids=lambda p: p.stem)
def test_open_harness_raises_no_hop_error(task_path):
    """DoD 1: the fault-wired harness itself answers -- no hop to the probe
    raises, for any open seed."""
    loaded = load_task(str(task_path))
    runpy.run_path(str(PASSING_DMM_DRIVER))
    for seed in _open_seeds(task_path):
        rail = _open_rail(loaded, seed)
        case = resolve_case(_probe_for(loaded, rail).case)
        realized = fault_mod.realized_fault(loaded.card, seed)
        topology = fault_mod.harness_for_run(case, rail=rail, realized=realized, seed=seed)
        bench = next(iter(topology["root"].values()))
        assert all("fault" not in c for c in bench["children"].values())
        with load(topology) as hal:
            node = next(n for root in hal._roots for n in root.walk() if n.id == "unit")
            reading = node.driver.measure_voltage()
        assert abs(reading) < 0.05 * rail.nominal_v, (task_path.name, seed, reading)


def test_open_seed_gives_the_same_reading_twice(tmp_path):
    """DoD 4: the same open seed realizes the same reading -- twice in one
    run, and again in a fresh run."""
    seed = _open_seeds(SAMPLE_TASK)[0]
    first, _ = _measure_open(SAMPLE_TASK, seed, tmp_path / "a")
    again, _ = _measure_open(SAMPLE_TASK, seed, tmp_path / "b")
    assert first == again

    loaded, _drives, _vin = _parts(SAMPLE_TASK)
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path / "c")["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path / "c")
    readings = [take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER,
                                 state_dir=tmp_path / "c")["reading"] for _ in range(2)]
    assert readings == [first, first]
    rail = _open_rail(loaded, seed)
    assert fault_mod.open_residual_v(rail, seed) == first


def test_open_seed_scores_as_a_caught_card_fault(tmp_path):
    """CTO comment on #477: an `open` seed answered `open` scores
    `faults_caught: 1` and `error_fail_correct: 0`."""
    seed = _open_seeds(SAMPLE_TASK)[0]
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    out = answer(run_id, "open", state_dir=tmp_path)
    assert out["correct"] is True and out["disqualified"] is False
    assert out["score"]["faults_caught"] == 1
    assert out["score"]["error_fail_correct"] == 0


def test_virtual_bench_unplugged_dmm_stays_error_transport(tmp_path):
    """DoD 3: `SHAL_SIM_UNPLUG=dmm` on the virtual bench is `error` /
    `cause: transport` -- `_run_virtual_bench_unplug_dmm` keeps its result."""
    bench_yaml = tmp_path / "bench.yaml"
    bench_yaml.write_text(demo._BENCH_TOPOLOGY_YAML, encoding="utf-8")
    result = demo._run_virtual_bench_unplug_dmm({"bench_yaml": bench_yaml})
    assert result["verdict"] == "error"
    assert result["cause"] == "transport"
    assert demo.check_virtual_bench_unplug_dmm(result)


def test_unplugged_arena_dmm_is_error_never_open_or_fail(tmp_path, monkeypatch):
    """DoD 3: a real unplug of the arena DMM raises (HopError under
    MeasurementFailed), is logged `cause: transport`, and neither diagnosis
    names `open` -- or any card fault -- for it."""
    from shal_arena.simlog import SimLog
    from shal_arena.store import RunStore

    loaded = load_task(str(SAMPLE_TASK))
    rail = loaded.card.rails[0]
    allowed = set(loaded.task.question.answer.values)
    card_faults = {f.id for f in loaded.card.faults} | {"fail"}
    for seed in (_open_seeds(SAMPLE_TASK)[0], loaded.task.seed):
        state_dir = tmp_path / str(seed)
        run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=state_dir)["run_id"]
        drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
        monkeypatch.setenv("SHAL_SIM_UNPLUG", _DMM_NODE_ID)
        with pytest.raises(MeasurementFailed) as info:
            take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
        monkeypatch.delenv("SHAL_SIM_UNPLUG")
        assert isinstance(info.value.__cause__, HopError)
        assert demo._failure_cause(info.value) == "transport"
        entries = SimLog(RunStore(state_dir).sim_log_path(run_id)).entries()
        assert any(e.get("kind") == "failed" and e.get("cause") == "transport"
                   for e in entries)

        given_demo = demo._diagnose(rail, None, allowed)
        given_story = story._diagnose(rail.nominal_v, rail.tol_pct, [], allowed)
        assert given_demo == given_story == "error"
        assert given_demo not in card_faults


def test_story_names_the_failure_cause_from_the_measure_error():
    """CTO review on #480: `story.py` reads the cause from `measure`'s own
    error JSON -- a HopError is `transport`, a driver bug is `driver`."""
    hop = {"ok": False, "error": {"type": "MeasurementFailed",
                                  "message": "measure_voltage raised HopError: no answer"}}
    bug = {"ok": False, "error": {"type": "MeasurementFailed",
                                  "message": "measure_voltage raised ValueError: bad reply"}}
    assert story._measure_failure_cause(hop) == "transport"
    assert story._measure_failure_cause(bug) == "driver"
