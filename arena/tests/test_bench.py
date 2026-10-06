"""issue #314: benchmark mode — the same task with SHAL and without SHAL.
Every assertion here is end to end against the public `runner`/`bench`
API (or the CLI subprocess, for the --runs refusal), never against a mock of
the sim: the point of this ticket is that the two sides are the SAME world,
so a test that faked either side would prove nothing."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena.bench import MIN_RUNS, run_benchmark, run_side, summarize
from shal_arena.errors import ArenaError, MeasurementFailed, TooFewRuns
from shal_arena.runner import answer, drive_input, pick_fault, raw_scpi, start_run
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

_TIMEOUT = 30


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TIMEOUT)


def _seed_not_open() -> int:
    from shal_arena.loader import load_task
    card = load_task(SAMPLE_TASK).card
    return next(s for s in range(100) if pick_fault(card, s) != "open")


_CLI_POLICY = """\
from shal_arena.runner import answer, drive_input, raw_scpi, start_run

def play_with_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    return run_id, answer(run_id, "ok", state_dir=state_dir)

def play_without_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 5.0", state_dir=state_dir)
    return run_id, answer(run_id, "ok", state_dir=state_dir)
"""


# -- a scripted fake agent: no model needed (Done-when) ---------------------- #
# Drives psu0 to the card's nominal 5.0 V, measures dmm0 once (through a
# driver.py with SHAL; through the raw command the dmm datasheet documents
# without it), and answers 'open' if that measurement couldn't be taken at
# all (the 'open' fault) or 'ok' otherwise — not meant to score well, only to
# exercise both access paths identically across many seeds, 'open' included.

def _play_with_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    given = "ok"
    try:
        from shal_arena.runner import take_measurement
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    except ArenaError:
        given = "open"
    record = answer(run_id, given, state_dir=state_dir)
    return run_id, record


def _play_without_shal(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 5.0", state_dir=state_dir)
    given = "ok"
    try:
        raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=state_dir)
    except ArenaError:
        given = "open"
    record = answer(run_id, given, state_dir=state_dir)
    return run_id, record


def test_bench_plays_both_sides_with_a_scripted_agent_no_model_needed(tmp_path: Path) -> None:
    result = run_benchmark(str(SAMPLE_TASK), play_with_shal=_play_with_shal,
                           play_without_shal=_play_without_shal, runs=MIN_RUNS,
                           state_dir=tmp_path)
    assert result["ok"] is True
    assert result["with_shal"]["runs"] == MIN_RUNS
    assert result["without_shal"]["runs"] == MIN_RUNS
    # same seeds, same task, same CardSim/fault model on both sides (Scope) ->
    # the same fraction of the batch realizes each fault on each side, so the
    # scripted agent above (identical decisions on both sides) scores the same.
    assert result["with_shal"]["correct"] == result["without_shal"]["correct"]


def test_output_contains_median_and_range_per_side(tmp_path: Path) -> None:
    result = run_benchmark(str(SAMPLE_TASK), play_with_shal=_play_with_shal,
                           play_without_shal=_play_without_shal, runs=MIN_RUNS,
                           state_dir=tmp_path)
    assert result["side_effect"] == "write"
    for side in ("with_shal", "without_shal"):
        summary = result[side]
        assert isinstance(summary["median_turns"], (int, float))
        lo, hi = summary["turns_range"]
        assert lo <= summary["median_turns"] <= hi
        assert summary["disqualified"] == 0   # the scripted agent above always probes
        assert summary["destroyed"] == 0      # ... and never drives past the card's limit
        assert len(summary["sim_logs"]) == MIN_RUNS
        for p in summary["sim_logs"]:
            assert Path(p).is_file()


def test_bench_refuses_fewer_than_min_runs_naming_the_fix(tmp_path: Path) -> None:
    with pytest.raises(TooFewRuns) as ei:
        run_benchmark(str(SAMPLE_TASK), play_with_shal=_play_with_shal,
                      play_without_shal=_play_without_shal, runs=9, state_dir=tmp_path)
    assert "9" in ei.value.message
    assert ei.value.fix
    # refused before either side plays a single run
    assert not any(tmp_path.rglob("*.json"))


def test_bench_cli_runs_9_is_refused_with_fix_in_json(tmp_path: Path) -> None:
    policy = tmp_path / "policy.py"
    policy.write_text(_CLI_POLICY, encoding="utf-8")
    proc = _run_cli("bench", str(SAMPLE_TASK), "--runs", "9", "--policy", str(policy),
                    "--state-dir", str(tmp_path / "state"), "--json")
    assert proc.returncode != 0
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False
    assert doc["error"]["type"] == "TooFewRuns"
    assert "9" in doc["error"]["message"]
    assert doc["error"]["fix"]


def test_bench_cli_end_to_end_json(tmp_path: Path) -> None:
    """The Agent path (issue #314): one non-interactive command, no prompt,
    a --policy file supplying the agent under test — same non-interactive
    contract as every other shal-arena command (cli.py's own docstring)."""
    policy = tmp_path / "policy.py"
    policy.write_text(_CLI_POLICY, encoding="utf-8")
    proc = _run_cli("bench", str(SAMPLE_TASK), "--runs", str(MIN_RUNS), "--policy", str(policy),
                    "--state-dir", str(tmp_path / "state"), "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    for side in ("with_shal", "without_shal"):
        assert doc[side]["runs"] == MIN_RUNS
        assert "median_turns" in doc[side]
        assert "turns_range" in doc[side]
        assert len(doc[side]["sim_logs"]) == MIN_RUNS


# -- turn counting: the CTO's ruling, both sides (Done-when) ----------------- #

def test_drive_n_times_counts_n_turns_on_both_sides_answer_does_not(tmp_path: Path) -> None:
    n = 4
    with_dir = tmp_path / "with_shal"
    without_dir = tmp_path / "without_shal"

    with_run = start_run(str(SAMPLE_TASK), seed=1, state_dir=with_dir)["run_id"]
    for _ in range(n):
        drive_input(with_run, "psu0", 5.0, state_dir=with_dir)
    assert RunStore(with_dir).load(with_run).turns == n
    answer(with_run, "ok", state_dir=with_dir)
    assert RunStore(with_dir).load(with_run).turns == n   # answer is not a turn

    without_run = start_run(str(SAMPLE_TASK), seed=1, state_dir=without_dir)["run_id"]
    for _ in range(n):
        raw_scpi(without_run, "psu0", "VOLT 5.0", state_dir=without_dir)
    assert RunStore(without_dir).load(without_run).turns == n
    answer(without_run, "ok", state_dir=without_dir)
    assert RunStore(without_dir).load(without_run).turns == n   # answer is not a turn


def test_a_bad_raw_command_still_costs_its_turn(tmp_path: Path) -> None:
    """Same rule as `check`/`measure`/`drive` on the SHAL side (issue #325):
    the turn is counted before the command is even parsed."""
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    with pytest.raises(ArenaError):
        raw_scpi(run_id, "psu0", "NOT A REAL COMMAND", state_dir=tmp_path)
    assert RunStore(tmp_path).load(run_id).turns == 1


# -- same sim log format on both sides (Done-when) ---------------------------- #

def test_both_sides_produce_a_sim_log_in_the_same_format(tmp_path: Path) -> None:
    from shal_arena.runner import take_measurement

    with_dir = tmp_path / "with_shal"
    without_dir = tmp_path / "without_shal"
    seed = _seed_not_open()

    with_run = start_run(str(SAMPLE_TASK), seed=seed, state_dir=with_dir)["run_id"]
    take_measurement(with_run, "dmm0", PASSING_DMM_DRIVER, state_dir=with_dir)
    with_lines = [json.loads(ln) for ln in
                 RunStore(with_dir).sim_log_path(with_run).read_text().splitlines()]

    without_run = start_run(str(SAMPLE_TASK), seed=seed, state_dir=without_dir)["run_id"]
    raw_scpi(without_run, "dmm0", "MEAS:VOLT:DC?", state_dir=without_dir)
    without_lines = [json.loads(ln) for ln in
                     RunStore(without_dir).sim_log_path(without_run).read_text().splitlines()]

    # the SET of entry kinds for the same act must match on both sides — a
    # query-only comparison (CTO review on #328) missed that `raw_scpi` wrote
    # no `measure` marker at all, which silently disqualified every
    # without-SHAL answer (see test_raw_scpi_measure_marker_not_disqualified).
    assert {ln["kind"] for ln in with_lines} == {ln["kind"] for ln in without_lines} == {
        "measure", "query", "reading"}

    with_query = next(ln for ln in with_lines if ln["kind"] == "query")
    without_query = next(ln for ln in without_lines if ln["kind"] == "query")
    assert set(with_query) == set(without_query) == {"ts", "address", "kind", "cmd"}
    assert with_query["cmd"] == without_query["cmd"] == "MEAS:VOLT:DC?"
    assert with_query["address"] == without_query["address"] == "dmm0"

    with_measure = next(ln for ln in with_lines if ln["kind"] == "measure")
    without_measure = next(ln for ln in without_lines if ln["kind"] == "measure")
    assert set(with_measure) == set(without_measure) == {"ts", "address", "kind"}


def test_without_shal_damage_logs_in_the_same_shape_as_with_shal(tmp_path: Path) -> None:
    """The SHAL gate (issue #330) refuses the 6.5 V drive, so its sim log line is
    `refused`, not `damage` — same fields, same act, different outcome."""
    with_dir = tmp_path / "with_shal"
    without_dir = tmp_path / "without_shal"

    with_run = start_run(str(SAMPLE_TASK), state_dir=with_dir)["run_id"]
    drive_input(with_run, "psu0", 6.5, state_dir=with_dir)  # buck-5v-3v3: destroyed above 6.0 V
    with_damage = next(json.loads(ln) for ln in
                       RunStore(with_dir).sim_log_path(with_run).read_text().splitlines()
                       if json.loads(ln)["kind"] == "refused")

    without_run = start_run(str(SAMPLE_TASK), state_dir=without_dir)["run_id"]
    raw_scpi(without_run, "psu0", "VOLT 6.5", state_dir=without_dir)
    without_damage = next(json.loads(ln) for ln in
                          RunStore(without_dir).sim_log_path(without_run).read_text().splitlines()
                          if json.loads(ln)["kind"] == "damage")

    assert set(with_damage) - {"kind"} == set(without_damage) - {"kind", "limit_v", "source"}
    assert with_damage["input"] == without_damage["input"] == "vin"
    assert with_damage["volts"] == without_damage["volts"] == 6.5


# -- the fault answer must never be on the player's disk, on the raw path too - #

def test_raw_scpi_never_writes_the_fault_to_disk(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    fault_id = pick_fault(_card_for(SAMPLE_TASK), RunStore(tmp_path).load(run_id).seed)
    for _ in range(3):
        try:
            raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=tmp_path)
        except MeasurementFailed:
            pass
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert fault_id not in path.read_text(encoding="utf-8"), (
                f"{path} contains the hidden fault {fault_id!r}")


def _card_for(task_path: Path):
    from shal_arena.loader import load_task
    return load_task(task_path).card


# -- raw_scpi error shapes ----------------------------------------------------- #

def test_raw_scpi_unknown_address_names_the_fix(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    with pytest.raises(ArenaError) as ei:
        raw_scpi(run_id, "no-such-address", "MEAS:VOLT:DC?", state_dir=tmp_path)
    assert "psu0" in ei.value.fix and "dmm0" in ei.value.fix


def test_raw_scpi_bad_drive_command_names_the_fix(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    with pytest.raises(ArenaError) as ei:
        raw_scpi(run_id, "psu0", "SET VOLTAGE TO FIVE", state_dir=tmp_path)
    assert "datasheet" in ei.value.fix


def test_raw_scpi_open_fault_raises_live_never_persisted(tmp_path: Path) -> None:
    from shal_arena.loader import load_task

    card = load_task(SAMPLE_TASK).card
    seed = next(s for s in range(300) if pick_fault(card, s) == "open")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    with pytest.raises(MeasurementFailed):
        raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=tmp_path)
    assert RunStore(tmp_path).load(run_id).turns == 1


def test_run_side_and_summarize_compose(tmp_path: Path) -> None:
    results = run_side(str(SAMPLE_TASK), _play_without_shal, runs=MIN_RUNS, seed_base=0,
                       state_dir=tmp_path)
    summary = summarize(results)
    assert summary["runs"] == MIN_RUNS
    assert summary["median_turns"] >= 1


# -- CTO review on #328 ------------------------------------------------------- #

def test_raw_scpi_measure_marker_not_disqualified(tmp_path: Path) -> None:
    """A without-SHAL probe must leave the same 'measure' marker a with-SHAL
    one does, or `answer`'s disqualification check (no marker at any probe
    address) fires on every raw run regardless of how it answered."""
    seed = _seed_not_open()
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=tmp_path)
    fault_id = pick_fault(_card_for(SAMPLE_TASK), seed)
    record = answer(run_id, fault_id, state_dir=tmp_path)
    assert record["correct"] is True
    assert record["disqualified"] is False


def test_raw_scpi_reports_write_even_for_a_query(tmp_path: Path) -> None:
    """Every runner call that reaches the sim mutates the run's own record (a
    turn, a sim log line) even when the underlying instrument op is a read —
    `check`/`measure`/`drive` all already report 'write' for the same reason."""
    run_id = start_run(str(SAMPLE_TASK), state_dir=tmp_path)["run_id"]
    out = raw_scpi(run_id, "dmm0", "MEAS:VOLT:DC?", state_dir=tmp_path)
    assert out["side_effect"] == "write"


def test_bench_counts_a_destroyed_card_as_a_failed_run(tmp_path: Path) -> None:
    """Scope: '30 V on a 5 V card destroys it and the task fails' — a
    destroyed card is a failed run even when the final answer happens to name
    the right fault; `correct` must not paper over the damage."""
    def _play_destroy(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
        run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
        raw_scpi(run_id, "psu0", "VOLT 6.5", state_dir=state_dir)  # destroys: > 6.0 V abs max
        fault_id = pick_fault(_card_for(Path(task_path)), seed)
        return run_id, answer(run_id, fault_id, state_dir=state_dir)

    results = run_side(str(SAMPLE_TASK), _play_destroy, runs=MIN_RUNS, seed_base=0,
                       state_dir=tmp_path)
    summary = summarize(results)
    assert summary["destroyed"] == MIN_RUNS
    assert summary["correct"] == 0


def test_bench_destroyed_only_without_shal_when_both_sides_send_30v(tmp_path: Path) -> None:
    """issue #330: the same 30 V on a 5 V card — the SHAL gate blocks it, the raw
    side destroys the card and fails the run."""
    def _fault_answer(run_id: str, task_path: str, seed: int, state_dir: str) -> dict:
        return answer(run_id, pick_fault(_card_for(Path(task_path)), seed), state_dir=state_dir)

    def _with(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
        run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
        drive_input(run_id, "psu0", 30.0, state_dir=state_dir)
        return run_id, _fault_answer(run_id, task_path, seed, state_dir)

    def _without(task_path: str, seed: int, state_dir: str) -> tuple[str, dict]:
        run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
        raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=state_dir)
        return run_id, _fault_answer(run_id, task_path, seed, state_dir)

    result = run_benchmark(str(SAMPLE_TASK), play_with_shal=_with, play_without_shal=_without,
                           runs=MIN_RUNS, state_dir=tmp_path)
    assert result["with_shal"]["destroyed"] == 0
    assert result["without_shal"]["destroyed"] == MIN_RUNS
    assert result["without_shal"]["correct"] == 0
