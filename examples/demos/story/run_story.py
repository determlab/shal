#!/usr/bin/env python3
"""The whole story, one script (issue #343): a virtual bench pass, an
unplugged DMM giving an error, a 30 V request blocked by the PSU's own
declared limit, the three SHAL Arena tasks (easy, medium, hard, each
measured for real and answered from that reading), a result card for the
last one, then the rail benchmark run ten times with the gate on and with
the gate off.

This installs nothing itself. A fresh venv needs exactly two wheels,
``pyshal`` and ``shal-arena`` (the release-candidate build, D1, until both
are on PyPI — see this directory's README). Everything this script reads at
run time comes from those two installed packages, not from a checkout:
SHAL Arena's tasks and cards are its own package data
(``importlib.resources``, not ``arena/src/...`` on disk), and the bench
topology below is the same device tree as
``examples/demos/virtual-bench/bench.yaml`` embedded verbatim — that
example itself ships in no wheel yet (issue #384 is the ticket to fix
that; until it lands, a checkout-free run needs its own copy).

Every step prints one plain line before it runs (no claim about speed or
score — a step either did what it was there to show, or it did not; the
arena steps measure the rail for real and report correct/wrong/
disqualified, never a bare "it ran"). ``--pause 0`` and ``--json`` exist
for CI: no waiting, and one JSON document on stdout instead of the
narration, right after this module's one fixed first line.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any

FIRST_LINE = "Simulated instruments only. Nothing here touches real hardware."

# The same device tree as examples/demos/virtual-bench/bench.yaml (issue
# #305, T8), embedded so this script never reads the checkout (see the
# module docstring: that example is not in any wheel until issue #384).
_BENCH_TOPOLOGY_YAML = """\
shal_version: 1
root:
  bench:
    driver: shal,sim-scpi
    address: sim0
    children:
      psu0:
        id: psu
        driver: shal,sim-psu
        address: psu0
        config:
          load_ohms: 10
          limits:
            set_voltage:
              volts: {maximum: 3.6}
      dmm0:
        id: dmm
        driver: shal,sim-dmm
        address: dmm0
        config:
          probe: psu0
"""

# The packaged reference driver for the arena "dmm" case (arena/tests/
# fixtures/drivers/passing_dmm_driver.py): an honest read of the probed
# rail, the same shape the Arena's own docs point a player at.
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

# The same idea as dev/e2e/story.py's own bench policy (shal#340): both sides
# attempt the same 30 V setpoint on psu0 (the card's 'vin'). SHAL's gate
# refuses it; the raw side has no gate and destroys the card.
_BENCH_POLICY_SOURCE = '''\
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


@contextlib.contextmanager
def _arena_task_file(level: str):
    """A real filesystem path to ``shal_arena``'s own packaged ``tasks/
    <level>.yaml`` — package data (``arena/pyproject.toml``
    ``[tool.setuptools.package-data]``), read through ``importlib.resources``
    so this never touches a checkout path."""
    ref = importlib_resources.files("shal_arena") / "tasks" / f"{level}.yaml"
    with importlib_resources.as_file(ref) as path:
        yield path


# -- pure checks (each takes exactly the result dict its own run_* function
# produces, decides pass/fail, and nothing else -- so a step whose JSON
# disagrees with what it claims is caught here, not by eyeballing output) -- #


def check_virtual_bench_pass(result: dict[str, Any]) -> bool:
    return result.get("verdict") == "pass"


def check_virtual_bench_unplug_dmm(result: dict[str, Any]) -> bool:
    return result.get("verdict") == "error"


def check_psu_30v_blocked(result: dict[str, Any]) -> bool:
    return result.get("blocked") == "limits"


def check_arena_task(result: dict[str, Any]) -> bool:
    return bool(result.get("correct")) and not result.get("disqualified")


def check_arena_result_card(result: dict[str, Any]) -> bool:
    return bool(result.get("card_path"))


def check_bench_10_runs(result: dict[str, Any]) -> bool:
    return (result.get("with_shal_destroyed") == 0
            and (result.get("without_shal_destroyed") or 0) > 0)


# -- the real world: shal's and shal_arena's own Python APIs --------------- #


def _run_virtual_bench_pass(ctx: dict[str, Any]) -> dict[str, Any]:
    import shal

    hal = shal.load(str(ctx["bench_yaml"]))
    try:
        psu = hal.get_device("psu")
        dmm = hal.get_device("dmm")
        with shal.approver(shal.AutoApprove()):
            psu.set_voltage(3.3)
        volts = dmm.measure_voltage()
    finally:
        hal.close()
    verdict = "pass" if 3.3 * 0.98 <= volts <= 3.3 * 1.02 else "fail"
    return {"verdict": verdict, "volts": volts}


def _run_virtual_bench_unplug_dmm(ctx: dict[str, Any]) -> dict[str, Any]:
    import shal

    with _env_var("SHAL_SIM_UNPLUG", "dmm"):
        hal = shal.load(str(ctx["bench_yaml"]))
        try:
            dmm = hal.get_device("dmm")
            dmm.measure_voltage()
        except shal.HopError as e:
            return {"verdict": "error", "message": str(e)}
        else:
            return {"verdict": "pass"}
        finally:
            hal.close()


def _run_psu_30v_blocked(ctx: dict[str, Any]) -> dict[str, Any]:
    import shal

    hal = shal.load(str(ctx["bench_yaml"]))
    try:
        psu = hal.get_device("psu")
        with shal.approver(shal.AutoApprove()):
            psu.set_voltage(30.0)
    except shal.LimitError as e:
        return {"blocked": "limits", "message": str(e), "violations": e.violations}
    else:
        return {"blocked": None}
    finally:
        hal.close()


def _run_arena_task(ctx: dict[str, Any], level: str) -> dict[str, Any]:
    from shal_arena.errors import MeasurementFailed
    from shal_arena.loader import load_task
    from shal_arena.runner import answer, drive_input, start_run, take_measurement

    with _arena_task_file(level) as task_path:
        loaded = load_task(str(task_path))
        task, card = loaded.task, loaded.card
        drives = next(i for i in task.instruments if i.drives is not None)
        probe = next(i for i in task.instruments if i.probe is not None)
        vin = next(i for i in card.inputs if i.name == drives.drives.split(".", 1)[1])
        rail = next(r for r in card.rails
                   if r.test_point == probe.probe.split(".", 1)[1])

        state_dir = ctx["state_dir"] / level
        run_doc = start_run(str(task_path), state_dir=state_dir)
        run_id = run_doc["run_id"]
        drive_input(run_id, str(drives.address), vin.nominal_v, state_dir=state_dir)

        allowed = set(task.question.answer.values)
        band = rail.nominal_v * rail.tol_pct / 100
        try:
            reading = take_measurement(run_id, str(probe.address), str(ctx["dmm_driver"]),
                                       state_dir=state_dir)["reading"]
        except MeasurementFailed:
            given, reading = "open", None
        else:
            if reading < rail.nominal_v - band and "low_voltage" in allowed:
                given = "low_voltage"
            elif reading > rail.nominal_v + band and "high_voltage" in allowed:
                given = "high_voltage"
            else:
                given = "ok"

        answer_doc = answer(run_id, given, state_dir=state_dir)
        ctx["arena_runs"][level] = (run_id, state_dir)
        return {"run_id": run_id, "reading": reading, "given": given,
               "correct": answer_doc["correct"], "disqualified": answer_doc["disqualified"]}


def _run_arena_result_card(ctx: dict[str, Any]) -> dict[str, Any]:
    from shal_arena.replay.card import build_result_card

    run_id, state_dir = ctx["arena_runs"]["hard"]
    html = build_result_card(run_id, state_dir=state_dir)
    out_dir = ctx["state_dir"] / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.card.html"
    out_path.write_text(html, encoding="utf-8")
    return {"run_id": run_id, "card_path": str(out_path)}


def _run_bench_10_runs(ctx: dict[str, Any]) -> dict[str, Any]:
    from shal_arena.bench import import_policy, run_benchmark

    policy_path = ctx["state_dir"] / "story_bench_policy.py"
    policy_path.write_text(_BENCH_POLICY_SOURCE, encoding="utf-8")
    policy = import_policy(str(policy_path))
    with _arena_task_file("rail-3v3") as task_path:
        result = run_benchmark(str(task_path),
                               play_with_shal=policy.play_with_shal,
                               play_without_shal=policy.play_without_shal,
                               runs=10, state_dir=ctx["state_dir"] / "bench")
    return {"with_shal_destroyed": result["with_shal"]["destroyed"],
           "without_shal_destroyed": result["without_shal"]["destroyed"]}


_STEPS: list[tuple[str, str, Any, Any]] = [
    ("virtual_bench_pass",
     "Setting the bench power supply to 3.3 volts and reading it back on the "
     "simulated multimeter.",
     _run_virtual_bench_pass, check_virtual_bench_pass),
    ("virtual_bench_unplug_dmm",
     "Unplugging the simulated multimeter and reading it again.",
     _run_virtual_bench_unplug_dmm, check_virtual_bench_unplug_dmm),
    ("psu_30v_blocked",
     "Asking the power supply for 30 volts, above the board's declared limit.",
     _run_psu_30v_blocked, check_psu_30v_blocked),
    ("arena_easy", "Running the easy arena task: measuring the rail and answering from it.",
     lambda ctx: _run_arena_task(ctx, "easy"), check_arena_task),
    ("arena_medium", "Running the medium arena task: measuring the rail and answering from it.",
     lambda ctx: _run_arena_task(ctx, "medium"), check_arena_task),
    ("arena_hard", "Running the hard arena task: measuring the rail and answering from it.",
     lambda ctx: _run_arena_task(ctx, "hard"), check_arena_task),
    ("arena_result_card", "Writing the result card for the hard run.",
     _run_arena_result_card, check_arena_result_card),
    ("bench_10_runs",
     "Running the rail task ten times, with the gate on and with the gate off.",
     _run_bench_10_runs, check_bench_10_runs),
]


def _plain_outcome(result: dict[str, Any]) -> str:
    if "crashed" in result:
        return "crashed"
    if "verdict" in result:
        return result["verdict"]
    if "blocked" in result:
        return "blocked" if result["blocked"] else "not blocked"
    if "correct" in result:
        if result.get("disqualified"):
            return f"disqualified (answered {result.get('given')})"
        return f"{'correct' if result['correct'] else 'wrong'} ({result.get('given')})"
    if "card_path" in result:
        return f"wrote {result['card_path']}"
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
        msg = (f"run_story.py: cannot import shal_arena ({e}). "
               f"Install it first: pip install shal-arena")
        print(msg, file=sys.stderr)
        if args.json:
            print(json.dumps({"ok": False, "error": {
                "type": "MissingDependency", "message": msg,
                "fix": "pip install shal-arena"}}, indent=2))
        return 3

    # a plain (not context-managed) temp dir: it survives the process, same
    # as `shal-arena`'s own default `--state-dir` -- the CTO review on #343
    # wants the result card still there after the script exits, not deleted
    # on the way out.
    state_dir = Path(tempfile.mkdtemp(prefix="shal-story-"))
    bench_yaml = state_dir / "bench.yaml"
    bench_yaml.write_text(_BENCH_TOPOLOGY_YAML, encoding="utf-8")
    dmm_driver = state_dir / "dmm_driver.py"
    dmm_driver.write_text(_DMM_DRIVER_SOURCE, encoding="utf-8")
    ctx: dict[str, Any] = {"state_dir": state_dir, "bench_yaml": bench_yaml,
                           "dmm_driver": dmm_driver, "arena_runs": {}}

    steps: list[dict[str, Any]] = []
    overall_ok = True
    for step_id, line, run_fn, check_fn in _STEPS:
        time.sleep(args.pause)
        if not args.json:
            print(line)
        try:
            result = run_fn(ctx)
            step_ok = check_fn(result)
        except Exception as e:  # noqa: BLE001 - one step's crash must not stop the story
            result, step_ok = {"crashed": f"{type(e).__name__}: {e}"}, False
        overall_ok = overall_ok and step_ok
        if not args.json:
            print(f"  -> {_plain_outcome(result)}")
        steps.append({"step": step_id, "line": line, "result": result})

    if not args.json:
        print(f"Run state (including the result card) is under {state_dir}")

    if args.json:
        print(json.dumps({"ok": overall_ok, "state_dir": str(state_dir), "steps": steps},
                         indent=2, default=str))
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
