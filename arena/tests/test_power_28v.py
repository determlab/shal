"""issue #491: the `power-28v` practice task -- a 28 V card through relay K1,
three rails (12 V at TP1, 5 V at TP2, 3.3 V at TP3) read by one DMM moved
between points (`probes:` + `--at`), a temperature sensor on U2, 4 faults.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from shal_arena import fault as fault_mod
from shal_arena.card_sim import DAMAGE, CardSim
from shal_arena.card_sim.model import PROTECTION
from shal_arena.errors import CheckCouldNotRun, TaskFormatError
from shal_arena.loader import list_tasks, load_task
from shal_arena.runner import (
    answer,
    call_op,
    drive_input,
    start_run,
    take_measurement,
)
from shal_arena.schema import validate_task
from shal_arena.simlog import SimLog
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload

from .conftest import (
    PASSING_DMM_DRIVER,
    PASSING_RELAY_DRIVER,
    PASSING_TEMP_DRIVER,
    RELAY_RAIL_TASK,
    SAMPLE_TASK,
)

_PKG = Path(__file__).resolve().parents[1] / "src" / "shal_arena"
TASK = _PKG / "tasks" / "power-28v.yaml"
CARD = _PKG / "cards" / "power-28v.yaml"
_LOADED = load_task(TASK)
_RAILS = {r.test_point: r for r in _LOADED.card.rails}
_U2 = next(t for t in _LOADED.card.temp_points if t.name == "u2")
_POINTS = ("tp_12v", "tp_5v", "tp_3v3")
_FAULTS = ("ok", "low_5v", "open_12v", "low_3v3", "overheat_u2")


def _seed_for(fault_id: str) -> int:
    return next(s for s in range(200)
                if fault_mod.realized_fault(_LOADED.card, s).fault_id == fault_id)


def _in_tol(point: str, value: float) -> bool:
    rail = _RAILS[point]
    return rail.nominal_v * (1 - rail.tol_pct / 100) <= value <= \
        rail.nominal_v * (1 + rail.tol_pct / 100)


def _powered_run(fault_id: str, state_dir: Path) -> str:
    """Start a run that realizes ``fault_id``, close the relay, drive 28.0 V."""
    run_id = start_run(str(TASK), seed=_seed_for(fault_id), state_dir=state_dir)["run_id"]
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "true"],
            state_dir=state_dir)
    out = drive_input(run_id, "psu0", 28.0, state_dir=state_dir)
    assert out["sent"] and out["state"] == "ok"
    return run_id


def _read_all(run_id: str, state_dir: Path) -> dict[str, float]:
    readings = {p: take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at=p,
                                    state_dir=state_dir)["reading"] for p in _POINTS}
    readings["u2"] = take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER,
                                      state_dir=state_dir)["reading"]
    return readings


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(_PKG.parent), os.environ.get("PYTHONPATH", "")])}
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)


# -- the files ---------------------------------------------------------------- #

def test_tasks_json_lists_power_28v_with_four_instruments(tmp_path: Path) -> None:
    proc = _cli(["tasks", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    listed = {t["name"]: t for t in json.loads(proc.stdout)["tasks"]}
    assert "power-28v" in listed and listed["power-28v"]["level"] == "hard"
    loaded = load_task(listed["power-28v"]["path"])
    assert len(loaded.task.instruments) == 4
    assert [t["name"] for t in list_tasks()].count("power-28v") == 1


def test_task_and_card_match_the_issue() -> None:
    task, card = _LOADED.task, _LOADED.card
    assert (task.level, task.limits.max_turns, task.limits.max_minutes) == ("hard", 60, 30)
    assert isinstance(task.seed, int)
    by_addr = {str(i.address): i for i in task.instruments}
    assert by_addr["psu0"].drives == "card.vin"
    assert by_addr["relay0"].switches == "card.vin"
    assert by_addr["temp0"].probe == "card.tp_u2_temp"
    assert by_addr["dmm0"].probes == ("card.tp_12v", "card.tp_5v", "card.tp_3v3")
    assert task.question.answer.values == _FAULTS
    assert {i.name: i.nominal_v for i in card.inputs} == {"vin": 28.0}
    assert {r.name: (r.nominal_v, r.tol_pct, r.test_point, r.label) for r in card.rails} == {
        "12v": (12.0, 3, "tp_12v", "TP1"), "5v": (5.0, 3, "tp_5v", "TP2"),
        "3v3": (3.3, 3, "tp_3v3", "TP3")}
    assert all(r.from_input == "vin" and r.min_input_v for r in card.rails)
    assert (_U2.test_point, _U2.nominal_c, _U2.high_c) == ("tp_u2_temp", 45.0, 85.0)
    assert {(d.above_v, d.effect) for d in card.damage} == {(29.5, "protection"),
                                                            (32.0, "destroyed")}
    sources = [r.tol_source for r in card.rails] + [_U2.tol_source] + \
        [d.source for d in card.damage]
    assert all("(fictional datasheet)" in s for s in sources)


# -- readings ----------------------------------------------------------------- #

def test_ok_reads_every_rail_in_tolerance_and_u2_below_its_limit(tmp_path: Path) -> None:
    readings = _read_all(_powered_run("ok", tmp_path), tmp_path)
    assert all(_in_tol(p, readings[p]) for p in _POINTS), readings
    assert readings["u2"] < _U2.high_c


@pytest.mark.parametrize("fault_id", ["low_5v", "open_12v", "low_3v3", "overheat_u2"])
def test_each_fault_gives_its_signature_and_nothing_else(fault_id: str, tmp_path: Path) -> None:
    readings = _read_all(_powered_run(fault_id, tmp_path), tmp_path)
    off = {"low_5v": "tp_5v", "open_12v": "tp_12v", "low_3v3": "tp_3v3"}.get(fault_id)
    for p in _POINTS:
        assert _in_tol(p, readings[p]) is (p != off), (fault_id, p, readings)
    if fault_id in ("low_5v", "low_3v3"):
        assert readings[off] < _RAILS[off].nominal_v * 0.97
    if fault_id == "open_12v":
        assert readings["tp_12v"] == pytest.approx(0.0, abs=0.05 * 12.0)
    if fault_id == "overheat_u2":
        assert readings["u2"] > _U2.high_c
    else:
        assert readings["u2"] < _U2.high_c


def test_relay_open_reads_every_rail_at_zero(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "false"],
            state_dir=tmp_path)
    for p in _POINTS:
        reading = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at=p,
                                   state_dir=tmp_path)["reading"]
        assert reading == pytest.approx(0.0, abs=1e-6), p


# -- --at (probe select) ------------------------------------------------------- #

def test_at_is_required_for_dmm0(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    with pytest.raises(CheckCouldNotRun) as exc:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    assert "--at is required" in str(exc.value)
    assert all(p in exc.value.fix for p in _POINTS)


def test_at_is_refused_on_a_single_probe_instrument(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    with pytest.raises(CheckCouldNotRun, match="--at is only for"):
        take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, at="tp_u2_temp",
                         state_dir=tmp_path)
    other = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    with pytest.raises(CheckCouldNotRun, match="--at is only for"):
        take_measurement(other, "dmm0", PASSING_DMM_DRIVER, at="tp_3v3", state_dir=tmp_path)


def test_unknown_point_lists_the_valid_points(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    with pytest.raises(CheckCouldNotRun) as exc:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at="tp_9v", state_dir=tmp_path)
    assert "tp_9v" in str(exc.value)
    assert all(p in str(exc.value) for p in _POINTS)


def test_cli_at_required_refused_unknown_and_accepted(tmp_path: Path) -> None:
    started = _cli(["run", "power-28v", "--seed", str(_seed_for("low_5v")), "--json"], tmp_path)
    assert started.returncode == 0, started.stderr
    run = json.loads(started.stdout)
    run_id = run["run_id"]
    dmm = next(i for i in run["instruments"] if i["address"] == "dmm0")
    assert dmm["probes"] == ["card.tp_12v", "card.tp_5v", "card.tp_3v3"]

    def measure(*extra: str) -> tuple[int, dict]:
        proc = _cli(["measure", run_id, "dmm0", str(PASSING_DMM_DRIVER), *extra, "--json"],
                    tmp_path)
        return proc.returncode, json.loads(proc.stdout)

    code, doc = measure()
    assert code != 0 and doc["ok"] is False and "--at" in doc["error"]["message"]
    code, doc = measure("--at", "tp_9v")
    assert code != 0 and all(p in doc["error"]["message"] for p in _POINTS)
    code, doc = measure("--at", "tp_5v")
    assert code == 0 and doc["point"] == "tp_5v" and doc["label"] == "TP2"
    assert doc["reading"] < 5.0 * 0.97

    proc = _cli(["measure", run_id, "temp0", str(PASSING_TEMP_DRIVER), "--at", "tp_u2_temp",
                 "--json"], tmp_path)
    assert proc.returncode != 0 and "--at" in json.loads(proc.stdout)["error"]["message"]


def test_probes_needs_two_known_points() -> None:
    doc = yaml.safe_load(TASK.read_text(encoding="utf-8"))
    doc["instruments"][1]["probes"] = ["card.tp_5v"]
    with pytest.raises(TaskFormatError, match="probes"):
        validate_task(doc)
    doc["instruments"][1]["probes"] = ["card.tp_5v", "card.tp_5v"]
    with pytest.raises(TaskFormatError, match="duplicate"):
        validate_task(doc)


# -- each reading is tied to its point ---------------------------------------- #

def test_each_reading_is_stored_with_its_point_and_named_in_log_and_timeline(
        tmp_path: Path) -> None:
    run_id = _powered_run("low_5v", tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at="tp_12v", state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at="tp_5v", state_dir=tmp_path)

    store = RunStore(tmp_path)
    stored = store.load(run_id).readings
    assert [(r["address"], r["point"]) for r in stored] == [("dmm0", "tp_12v"),
                                                           ("dmm0", "tp_5v")]
    assert stored[0]["value"] == pytest.approx(12.0) and stored[1]["value"] < 4.85

    log = [e for e in SimLog(store.sim_log_path(run_id)).entries()
           if e["kind"] in ("measure", "reading")]
    assert [(e["kind"], e["label"]) for e in log] == [
        ("measure", "TP1"), ("reading", "TP1"), ("measure", "TP2"), ("reading", "TP2")]

    # the timeline row names the point the same way the page's own step
    # title does ("Measured dmm0 at TP2"), and each reading carries it
    timeline = run_payload(run_id, state_dir=tmp_path)["timeline"]
    rows = [f"Measured {e['address']} at {e['detail']['label']}"
            for e in timeline if e["kind"] == "reading"]
    assert rows == ["Measured dmm0 at TP1", "Measured dmm0 at TP2"]
    from shal_arena.ui.page import _SCRIPT
    assert "Measured ${addr} at ${at}" in _SCRIPT

    # the closing sentence judges each point against its own limit
    answer(run_id, "low_5v", state_dir=tmp_path)
    sentence = run_payload(run_id, state_dir=tmp_path)["answer_sentence"]
    assert "the 5V rail reads" in sentence and "below its 4.85 V limit" in sentence
    assert sentence.endswith("Correct.")


def test_single_probe_log_lines_keep_their_old_shape(tmp_path: Path) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), state_dir=tmp_path)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    entries = SimLog(RunStore(tmp_path).sim_log_path(run_id)).entries()
    assert all("point" not in e and "label" not in e for e in entries
               if e["kind"] in ("measure", "reading"))


# -- damage ------------------------------------------------------------------- #

def test_overvoltage_trips_protection_then_destroys(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    tripped = drive_input(run_id, "psu0", 30.0, state_dir=tmp_path)
    assert tripped["sent"] and tripped["state"] == PROTECTION
    for p in _POINTS:
        assert take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at=p,
                                state_dir=tmp_path)["reading"] == pytest.approx(0.0, abs=1e-6)
    kinds = [e["kind"] for e in SimLog(RunStore(tmp_path).sim_log_path(run_id)).entries()]
    assert "protection" in kinds

    # the PSU caps at 30.0 V, so 32.0 V+ is reached on the card model itself
    log = tmp_path / "card.simlog.jsonl"
    sim = CardSim(yaml.safe_load(CARD.read_text(encoding="utf-8")), log_path=log)
    assert sim.apply_input("vin", 29.0).state == "ok"
    assert sim.apply_input("vin", 29.6).state == PROTECTION
    assert sim.apply_input("vin", 32.1).state == DAMAGE
    assert [json.loads(line)["kind"] for line in log.read_text().splitlines()] == [
        "write", "protection", "damage"]


def test_drive_above_the_psu_cap_is_refused_and_nothing_is_sent(tmp_path: Path) -> None:
    run_id = _powered_run("ok", tmp_path)
    out = drive_input(run_id, "psu0", 33.0, state_dir=tmp_path)
    assert out["sent"] is False and out["state"] == "ok"


# -- scoring ------------------------------------------------------------------ #

@pytest.mark.parametrize("fault_id", _FAULTS)
def test_answering_the_realized_fault_scores_correct_anything_else_wrong(
        fault_id: str, tmp_path: Path) -> None:
    for given in _FAULTS:
        state_dir = tmp_path / given
        run_id = _powered_run(fault_id, state_dir)
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, at="tp_5v", state_dir=state_dir)
        out = answer(run_id, given, state_dir=state_dir)
        assert out["correct"] is (given == fault_id), (fault_id, given)
        assert out["disqualified"] is False
