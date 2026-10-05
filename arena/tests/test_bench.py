"""Benchmark mode (issue #314): the same task with and without SHAL, a scripted
fake agent on both sides (no model needed)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shal_arena.bench import MIN_RUNS, BenchError, RawScpi, run_bench
from shal_arena.cli import main
from shal_arena.errors import MeasurementFailed
from shal_arena.runner import start_run
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _reader(env) -> None:
    """Drive 5 V twice, read the rail once, answer."""
    if env.side == "shal":
        env.drive("psu0", 5.0)
        env.drive("psu0", 5.0)
        env.measure("dmm0", PASSING_DMM_DRIVER)
    else:
        env.send("psu0", "VOLT 5.0")
        env.send("psu0", "VOLT 5.0")
        env.send("dmm0", "MEAS:VOLT:DC?")
    env.answer("ok")


def _destroyer(env) -> None:
    if env.side == "shal":
        env.drive("psu0", 30.0)
    else:
        env.send("psu0", "VOLT 30")
    env.answer("ok")


def _driver_n(n: int):
    def agent(env) -> None:
        for _ in range(n):
            if env.side == "shal":
                env.drive("psu0", 5.0)
            else:
                env.send("psu0", "VOLT 5.0")
        env.answer("ok")
    return agent


def test_both_sides_produce_a_sim_log_in_the_same_format(tmp_path: Path) -> None:
    out = run_bench(str(SAMPLE_TASK), _reader, runs=MIN_RUNS, state_dir=tmp_path)
    keys = {}
    for side in ("shal", "raw"):
        run = out["sides"][side]["runs"][0]
        lines = [json.loads(ln) for ln in
                 Path(run["sim_log"]).read_text(encoding="utf-8").splitlines()]
        assert lines
        # every command received carries a time, the address and the command
        commands = [ln for ln in lines if ln["kind"] in ("write", "query")]
        assert any(c["cmd"].startswith("VOLT") and c["address"] == "psu0" for c in commands)
        assert all({"ts", "address", "kind", "cmd"} <= c.keys() for c in commands)
        keys[side] = {frozenset(ln) for ln in lines}
    assert keys["shal"] & keys["raw"]  # at least one line shape in common


def test_runs_below_ten_are_refused_naming_the_fix(tmp_path: Path) -> None:
    with pytest.raises(BenchError) as ei:
        run_bench(str(SAMPLE_TASK), _reader, runs=9, state_dir=tmp_path)
    assert "--runs 10" in ei.value.fix
    assert not list(tmp_path.rglob("*.json"))  # nothing ran


def test_cli_refuses_runs_9(tmp_path: Path, capsys) -> None:
    rc = main(["bench", str(SAMPLE_TASK), "--agent", f"{__name__}:_reader", "--runs", "9",
               "--json", "--state-dir", str(tmp_path)])
    assert rc == 2
    err = json.loads(capsys.readouterr().out)["error"]
    assert err["type"] == "BenchError" and "--runs 10" in err["fix"]


@pytest.mark.parametrize("n", [1, 3, 7])
def test_n_drives_are_n_turns_on_both_sides_and_answer_is_not_a_turn(
        tmp_path: Path, n: int) -> None:
    out = run_bench(str(SAMPLE_TASK), _driver_n(n), runs=MIN_RUNS, state_dir=tmp_path)
    for side in ("shal", "raw"):
        assert [r["turns"] for r in out["sides"][side]["runs"]] == [n] * MIN_RUNS
        assert all(r["answered"] for r in out["sides"][side]["runs"])


def test_output_has_median_and_range_per_side(tmp_path: Path) -> None:
    out = run_bench(str(SAMPLE_TASK), _reader, runs=MIN_RUNS, state_dir=tmp_path)
    assert out["runs_per_side"] == MIN_RUNS
    for side in ("shal", "raw"):
        s = out["sides"][side]
        assert len(s["runs"]) == MIN_RUNS and len(s["sim_logs"]) == MIN_RUNS
        assert s["turns"] == {"median": 3, "min": 3, "max": 3}
        assert {"median", "min", "max"} <= s["seconds"].keys()


def test_over_limit_destroys_the_card_on_both_sides_and_fails_the_task(tmp_path: Path) -> None:
    out = run_bench(str(SAMPLE_TASK), _destroyer, runs=MIN_RUNS, state_dir=tmp_path)
    for side in ("shal", "raw"):
        s = out["sides"][side]
        assert s["destroyed"] == MIN_RUNS and s["success_rate"] == 0
        log = [json.loads(ln) for ln in
               Path(s["runs"][0]["sim_log"]).read_text(encoding="utf-8").splitlines()]
        assert any(ln["kind"] == "damage" for ln in log)


def test_raw_side_wins_are_reported_not_hidden(tmp_path: Path) -> None:
    """The raw agent reads once; the SHAL agent does the same plus a check call."""
    def agent(env) -> None:
        if env.side == "shal":
            env.check("dmm0", PASSING_DMM_DRIVER)
            env.measure("dmm0", PASSING_DMM_DRIVER)
        else:
            env.send("dmm0", "MEAS:VOLT:DC?")
        env.answer("ok")
    out = run_bench(str(SAMPLE_TASK), agent, runs=MIN_RUNS, state_dir=tmp_path)
    assert out["comparison"]["turns"] == "raw"


def test_same_seed_both_sides_and_raw_reading_follows_the_fault(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    raw = RawScpi(run_id, state_dir=tmp_path)
    try:
        reading = raw.send("dmm0", "MEAS:VOLT:DC?")
        assert 2.5 < float(reading) < 4.1
    except MeasurementFailed:  # the seed realized `open`: the instrument is silent
        pass
    assert RunStore(tmp_path).load(run_id).turns == 1


def test_raw_unknown_address_names_the_valid_ones(tmp_path: Path) -> None:
    from shal_arena.errors import CheckCouldNotRun
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    with pytest.raises(CheckCouldNotRun) as ei:
        RawScpi(run_id, state_dir=tmp_path).send("nope", "*IDN?")
    assert "psu0" in ei.value.fix


def test_cli_bench_json_returns_median_range_and_log_paths(tmp_path: Path, capsys) -> None:
    rc = main(["bench", str(SAMPLE_TASK), "--agent", f"{__name__}:_reader", "--runs", "10",
               "--json", "--state-dir", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    for side in ("shal", "raw"):
        assert out["sides"][side]["turns"]["median"] == 3
        assert len(out["sides"][side]["sim_logs"]) == 10
