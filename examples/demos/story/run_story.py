#!/usr/bin/env python3
"""The whole story, one script (issue #343): a virtual bench pass, an
unplugged DMM giving an error, a 30 V request blocked by the PSU's own
declared limit, the three SHAL Arena tasks (easy, medium, hard), then the
rail benchmark run ten times with the gate on and with the gate off.

This installs nothing itself. It reuses ``examples/demos/virtual-bench/``'s
topology (``bench.yaml``) through ``shal``'s own Python API — not
``run_bench.py``, which needs the unpublished ``pytest-shal`` this story
never asks a reader to install — and SHAL Arena's own ``shal_arena`` package.
A fresh venv needs exactly two packages: ``pyshal`` and ``shal-arena``,
neither on PyPI yet — install both from source (``examples/demos/story/
README.md#install``).

Every step prints one plain line before it runs, then one of: a real
measured verdict, ``correct``/``wrong``/``disqualified`` for an arena task,
or ``crashed`` — never a neutral word standing in for a bad result.
``--pause 0`` and ``--json`` exist for CI: no waiting, and one JSON document
on stdout instead of the narration, right after this module's one fixed
first line.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_YAML = REPO_ROOT / "examples" / "demos" / "virtual-bench" / "bench.yaml"
ARENA_TASKS_DIR = REPO_ROOT / "arena" / "src" / "shal_arena" / "tasks"

FIRST_LINE = "Simulated instruments only. Nothing here touches real hardware."

# The same idea as dev/e2e/story.py's own bench policy (shal#340): both sides
# attempt the same 30 V setpoint on psu0 (the card's 'vin'). SHAL's gate
# refuses it; the raw side has no gate and destroys the card.
_POLICY_SOURCE = '''\
from shal_arena.loader import load_task
from shal_arena.runner import answer, drive_input, pick_fault, raw_scpi, start_run


def _fault_answer(run_id, task_path, seed, state_dir):
    card = load_task(task_path).card
    return answer(run_id, pick_fault(card, seed), state_dir=state_dir)


def play_with_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 30.0, state_dir=state_dir)
    return run_id, _fault_answer(run_id, task_path, seed, state_dir)


def play_without_shal(task_path, seed, state_dir):
    run_id = start_run(task_path, seed=seed, state_dir=state_dir)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=state_dir)
    return run_id, _fault_answer(run_id, task_path, seed, state_dir)
'''

# A reference `driver.py` for the packaged "dmm" ADK case (same shape as
# arena/src/shal_arena/adk/dmm/docs/ documents, same registration
# arena/tests/fixtures/drivers/passing_dmm_driver.py uses) so an arena task
# step can take a real measurement rather than only naming an address.
_DMM_DRIVER_SOURCE = '''\
from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class BenchDmm1(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(BenchDmm1, override=True)
'''


def _diagnose(rail, reading: float | None, valid_values: set[str]) -> str:
    """A guess from the measurement alone: the rail's own documented nominal
    voltage and tolerance (never the hidden fault, which this process never
    reads). A reading outside tolerance but not clearly high or low falls
    back to ``noise`` when the task even offers it — this can still be the
    wrong fault name; that is the player's job to get right, not this
    demo's."""
    if reading is None:
        return "open"
    tol_v = rail.nominal_v * rail.tol_pct / 100.0
    delta = reading - rail.nominal_v
    if abs(delta) <= tol_v:
        return "ok"
    if delta < 0 and "low_voltage" in valid_values:
        return "low_voltage"
    if delta > 0 and "high_voltage" in valid_values:
        return "high_voltage"
    if "noise" in valid_values:
        return "noise"
    return "ok"


@contextlib.contextmanager
def _env_var(name: str, value: str):
    old = os.environ.get(name)
    os.environ[name] = value
    try:
        yield
    finally:
        if old is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = old


def _step_virtual_bench_pass(ctx: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    import shal

    hal = shal.load(str(BENCH_YAML))
    try:
        psu = hal.get_device("psu")
        dmm = hal.get_device("dmm")
        with shal.approver(shal.AutoApprove()):
            psu.set_voltage(3.3)
        volts = dmm.measure_voltage()
    finally:
        hal.close()
    verdict = "pass" if 3.3 * 0.98 <= volts <= 3.3 * 1.02 else "fail"
    return {"verdict": verdict, "volts": volts}, verdict == "pass"


def _step_virtual_bench_unplug_dmm(ctx: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    import shal

    with _env_var("SHAL_SIM_UNPLUG", "dmm"):
        hal = shal.load(str(BENCH_YAML))
        try:
            dmm = hal.get_device("dmm")
            dmm.measure_voltage()
        except shal.HopError as e:
            return {"verdict": "error", "message": str(e)}, True
        else:
            return {"verdict": "pass"}, False
        finally:
            hal.close()


def _step_psu_30v_blocked(ctx: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    import shal

    hal = shal.load(str(BENCH_YAML))
    try:
        psu = hal.get_device("psu")
        with shal.approver(shal.AutoApprove()):
            psu.set_voltage(30.0)
    except shal.LimitError as e:
        return {"blocked": "limits", "message": str(e), "violations": e.violations}, True
    else:
        return {"blocked": None}, False
    finally:
        hal.close()


def _step_arena_task(ctx: dict[str, Any], level: str) -> tuple[dict[str, Any], bool]:
    from shal_arena.errors import MeasurementFailed
    from shal_arena.loader import load_task
    from shal_arena.runner import answer, start_run, take_measurement

    task_path = ARENA_TASKS_DIR / f"{level}.yaml"
    loaded = load_task(task_path)
    task, card = loaded.task, loaded.card
    probe_instrument = next(i for i in task.instruments if i.probe is not None)
    rail = next(r for r in card.rails if f"card.{r.test_point}" == probe_instrument.probe)

    state_dir = ctx["state_dir"] / level
    state_dir.mkdir(parents=True, exist_ok=True)
    driver_path = state_dir / "dmm_driver.py"
    driver_path.write_text(_DMM_DRIVER_SOURCE, encoding="utf-8")

    run_doc = start_run(str(task_path), state_dir=state_dir)
    run_id = run_doc["run_id"]
    try:
        reading = take_measurement(run_id, str(probe_instrument.address), driver_path,
                                   state_dir=state_dir)["reading"]
    except MeasurementFailed:
        reading = None

    given = _diagnose(rail, reading, set(task.question.answer.values))
    answer_doc = answer(run_id, given, state_dir=state_dir)
    disqualified = answer_doc["disqualified"]
    correct = answer_doc["correct"]
    outcome = "disqualified" if disqualified else ("correct" if correct else "wrong")
    return {"run_id": run_id, "reading": reading, "given": given,
           "disqualified": disqualified, "correct": correct, "outcome": outcome}, not disqualified


def _step_bench_10_runs(ctx: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    from shal_arena.bench import import_policy, run_benchmark

    policy_path = ctx["state_dir"] / "story_bench_policy.py"
    policy_path.write_text(_POLICY_SOURCE, encoding="utf-8")
    policy = import_policy(str(policy_path))
    result = run_benchmark(str(ARENA_TASKS_DIR / "rail-3v3.yaml"),
                           play_with_shal=policy.play_with_shal,
                           play_without_shal=policy.play_without_shal,
                           runs=10, state_dir=ctx["state_dir"] / "bench")
    with_destroyed = result["with_shal"]["destroyed"]
    without_destroyed = result["without_shal"]["destroyed"]
    ok = with_destroyed == 0 and without_destroyed > 0
    return {"with_shal_destroyed": with_destroyed, "without_shal_destroyed": without_destroyed}, ok


_STEPS: list[tuple[str, str, Any]] = [
    ("virtual_bench_pass",
     "Setting the bench power supply to 3.3 volts and reading it back on the "
     "simulated multimeter.",
     _step_virtual_bench_pass),
    ("virtual_bench_unplug_dmm",
     "Unplugging the simulated multimeter and reading it again.",
     _step_virtual_bench_unplug_dmm),
    ("psu_30v_blocked",
     "Asking the power supply for 30 volts, above the board's declared limit.",
     _step_psu_30v_blocked),
    ("arena_easy", "Running the easy arena task and answering it.",
     lambda ctx: _step_arena_task(ctx, "easy")),
    ("arena_medium", "Running the medium arena task and answering it.",
     lambda ctx: _step_arena_task(ctx, "medium")),
    ("arena_hard", "Running the hard arena task and answering it.",
     lambda ctx: _step_arena_task(ctx, "hard")),
    ("bench_10_runs",
     "Running the rail task ten times, with the gate on and with the gate off.",
     _step_bench_10_runs),
]


def _plain_outcome(result: dict[str, Any]) -> str:
    if "crashed" in result:
        return "crashed"
    if "verdict" in result:
        return result["verdict"]
    if "blocked" in result:
        return "blocked" if result["blocked"] else "not blocked"
    if "outcome" in result:
        return result["outcome"]
    if "with_shal_destroyed" in result:
        return (f"gate on: {result['with_shal_destroyed']} destroyed, "
                f"gate off: {result['without_shal_destroyed']} destroyed")
    return "done"  # pragma: no cover - every step above sets a recognized key


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pause", type=float, default=2.0,
                        help="seconds to pause before each step (default: 2.0; use 0 for CI)")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON document on stdout instead of narrating")
    args = parser.parse_args(argv)

    print(FIRST_LINE)
    sys.stdout.flush()

    try:
        import shal_arena  # noqa: F401
    except ImportError as e:
        fix = ("shal-arena is not on PyPI yet; install it from source — see "
              "examples/demos/story/README.md#install")
        msg = f"run_story.py: cannot import shal_arena ({e}). {fix}"
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": {
                "type": "MissingDependency", "message": msg, "fix": fix}}, indent=2))
        return 3

    steps: list[dict[str, Any]] = []
    overall_ok = True
    with tempfile.TemporaryDirectory(prefix="shal-story-") as tmp:
        ctx = {"state_dir": Path(tmp)}
        for step_id, line, fn in _STEPS:
            time.sleep(args.pause)
            if not args.json:
                print(line)
            try:
                result, step_ok = fn(ctx)
            except Exception as e:  # noqa: BLE001 - one step's crash must not stop the story
                result, step_ok = {"crashed": f"{type(e).__name__}: {e}"}, False
            overall_ok = overall_ok and step_ok
            if not args.json:
                print(f"  -> {_plain_outcome(result)}")
            steps.append({"step": step_id, "line": line, "result": result})

    if args.json:
        print(json.dumps({"ok": overall_ok, "steps": steps}, indent=2, default=str))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
