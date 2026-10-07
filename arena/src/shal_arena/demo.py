"""The whole story, one script (issue #343), now shipped inside the package
itself (issue #410) so ``shal-arena demo`` runs it with no clone, no
config: a virtual bench pass, an unplugged DMM giving an error, a 30 V
request blocked by the PSU's own declared limit, the three SHAL Arena tasks
(easy, medium, hard, each measured for real and answered from that
reading), a result card for the last one, then the rail benchmark run ten
times with the gate on and with the gate off.

Everything this reads at run time comes from the installed ``shal_arena``
package, not a checkout: its tasks and cards are package data
(``importlib.resources``), and the bench topology below is the same device
tree as ``examples/demos/virtual-bench/bench.yaml`` embedded verbatim --
that example itself ships in no wheel (issue #384 is the ticket to fix
that; until it lands, a checkout-free run needs its own copy).
``examples/demos/story/run_story.py`` is now a thin wrapper around
`main` below, kept runnable directly for anyone who still has the repo
checked out.

Every step prints one plain line before it runs, then one of: a real
measured verdict, ``correct``/``wrong``/``disqualified`` for an arena task,
or ``crashed`` -- never a neutral word standing in for a bad result.
``--pause 0`` and ``--json`` exist for CI: no waiting, and one JSON document
on stdout instead of the narration, right after this module's one fixed
first line."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import tempfile
import time
import traceback
from importlib import resources as importlib_resources
from pathlib import Path
from typing import Any

from .store import RunStore

FIRST_LINE = "Simulated instruments only. Nothing here touches real hardware."

# The same device tree as examples/demos/virtual-bench/bench.yaml (issue
# #305, T8), embedded so this never reads the checkout (see the module
# docstring: that example is not in any wheel until issue #384).
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

#: issue #477: a rail reading below this fraction of its nominal is an open
#: circuit (``open``), never ``low_voltage``.
OPEN_FRACTION = 0.05


def _diagnose(rail, reading: float | None, allowed: set[str]) -> str:
    """A guess from the measurement alone: the rail's own documented nominal
    voltage and tolerance (never the hidden fault, which this process never
    reads). A reading near 0 V is ``open`` (issue #477: an open circuit on
    the card still answers). No reading at all (``None``: the read itself
    failed, a broken link to the bench) is ``error`` -- never ``open``,
    never a card fault. A reading outside tolerance but not clearly low or
    high falls back to ``noise`` when the task even offers it -- this can
    still be the wrong fault name; that is the player's job to get right,
    not this demo's."""
    if reading is None:
        return "error"
    if abs(reading) < OPEN_FRACTION * rail.nominal_v and "open" in allowed:
        return "open"
    band = rail.nominal_v * rail.tol_pct / 100
    delta = reading - rail.nominal_v
    if abs(delta) <= band:
        return "ok"
    if delta < 0 and "low_voltage" in allowed:
        return "low_voltage"
    if delta > 0 and "high_voltage" in allowed:
        return "high_voltage"
    if "noise" in allowed:
        return "noise"
    return "ok"


def _failure_cause(e: BaseException) -> str:
    """``transport`` when a failed measurement came from shal's own "the hop
    never completed" (`HopError`/`HopTimeout`), the same split
    `runner.take_measurement` logs; ``driver`` otherwise."""
    import shal

    hop = (shal.errors.HopError, shal.errors.HopTimeout)
    return "transport" if isinstance(e, hop) or isinstance(e.__cause__, hop) else "driver"


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
    <level>.yaml`` -- package data (``arena/pyproject.toml``
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
    return result.get("verdict") == "error" and result.get("cause") == "transport"


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
            # CTO review on #410: the same `cause` shal core itself writes
            # for a HopError step (`src/shal/record.py`) and the
            # virtual-bench sample's own `run_bench.py --unplug` already
            # prints -- this step's JSON matches that established shape.
            return {"verdict": "error", "cause": "transport", "message": str(e)}
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
    from .errors import MeasurementFailed
    from .loader import load_task
    from .runner import answer, drive_input, start_run, take_measurement

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
        # power the card first -- an unpowered rail reads near 0 V, which a
        # tolerance-band diagnosis would (correctly, but uselessly) call
        # "low_voltage" every time
        drive_input(run_id, str(drives.address), vin.nominal_v, state_dir=state_dir)

        try:
            reading = take_measurement(run_id, str(probe.address), str(ctx["dmm_driver"]),
                                       state_dir=state_dir)["reading"]
        except MeasurementFailed as e:
            # issue #477: no answer from the instrument is a broken link to
            # the bench -- `error`, never a card fault, so nothing is answered
            ctx["arena_runs"][level] = (run_id, state_dir)
            return {"run_id": run_id, "reading": None, "given": None,
                    "verdict": "error", "cause": _failure_cause(e), "message": str(e),
                    "correct": False, "disqualified": False}
        given = _diagnose(rail, reading, set(task.question.answer.values))

        answer_doc = answer(run_id, given, state_dir=state_dir)
        ctx["arena_runs"][level] = (run_id, state_dir)
        return {"run_id": run_id, "reading": reading, "given": given,
               "correct": answer_doc["correct"], "disqualified": answer_doc["disqualified"]}


def _run_arena_result_card(ctx: dict[str, Any]) -> dict[str, Any]:
    from .replay.card import build_result_card

    run_id, state_dir = ctx["arena_runs"]["hard"]
    html = build_result_card(run_id, state_dir=state_dir)
    out_dir = ctx["state_dir"] / "cards"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{run_id}.card.html"
    out_path.write_text(html, encoding="utf-8")
    record_path = RunStore(state_dir).record_path(run_id)
    ctx["record_path"] = str(record_path)
    return {"run_id": run_id, "card_path": str(out_path)}


def _run_bench_10_runs(ctx: dict[str, Any]) -> dict[str, Any]:
    # CTO review on #410: the same built-in policy `bench --runs 10` plays
    # with no `--policy` of its own (issue #397) -- not a second, separate
    # copy of "drive 30 V on purpose, answer from the reading" that could
    # drift from it. `_default_dmm_driver_registered()` scopes its driver
    # the same way the CLI's own `_cmd_bench` does.
    from .bench import DEFAULT_POLICY, _default_dmm_driver_registered, run_benchmark

    with _arena_task_file("rail-3v3") as task_path, _default_dmm_driver_registered():
        result = run_benchmark(str(task_path),
                               play_with_shal=DEFAULT_POLICY.play_with_shal,
                               play_without_shal=DEFAULT_POLICY.play_without_shal,
                               runs=10, state_dir=ctx["state_dir"] / "bench")
    return {"with_shal_destroyed": result["with_shal"]["destroyed"],
           "without_shal_destroyed": result["without_shal"]["destroyed"]}


# CTO review on #383 (the CMO's addition): the arena steps and the gate-on/
# off step are played by this script's own fixed logic (_diagnose, the
# bench policy above) -- never an AI agent -- so each of their lines says so
# up front. A viewer must not mistake a scripted number for an agent's.
_SCRIPTED_PLAYER_NOTE = "Scripted player, not an AI agent."

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
    ("arena_easy",
     f"{_SCRIPTED_PLAYER_NOTE} Running the easy arena task: measuring the rail "
     "and answering from it.",
     lambda ctx: _run_arena_task(ctx, "easy"), check_arena_task),
    ("arena_medium",
     f"{_SCRIPTED_PLAYER_NOTE} Running the medium arena task: measuring the rail "
     "and answering from it.",
     lambda ctx: _run_arena_task(ctx, "medium"), check_arena_task),
    ("arena_hard",
     f"{_SCRIPTED_PLAYER_NOTE} Running the hard arena task: measuring the rail "
     "and answering from it.",
     lambda ctx: _run_arena_task(ctx, "hard"), check_arena_task),
    ("arena_result_card", "Writing the result card for the hard run.",
     _run_arena_result_card, check_arena_result_card),
    ("bench_10_runs",
     f"{_SCRIPTED_PLAYER_NOTE} Running the rail task ten times, asking for 30 volts "
     "on purpose each time, with the gate on and with the gate off.",
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


def run_story(*, pause: float, json_mode: bool) -> int:
    """The whole story, run once: one plain line (or one JSON document) per
    step, then the final summary. Shared by `main` below (the standalone
    entry point) and `shal-arena demo` (``cli.py``'s own `_cmd_demo`), so
    both run the exact same code, not two copies that could drift."""
    # CTO review on #383: with --json, stdout must be ONE JSON document and
    # nothing else -- printing this line ahead of it broke `ConvertFrom-Json`
    # (and any other strict JSON reader). Plain mode still prints it first,
    # for a person watching (or the GIF); --json carries the same text as
    # the JSON's own "note" field instead.
    if not json_mode:
        print(FIRST_LINE)
        sys.stdout.flush()

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
        time.sleep(pause)
        if not json_mode:
            print(line)
        try:
            result = run_fn(ctx)
            step_ok = check_fn(result)
        except Exception as e:  # noqa: BLE001 - one step's crash must not stop the story
            # CTO review on #410: a crashed step names a fix too, the same
            # as any other error this CLI prints (module docstring:
            # "fix is never empty") -- the exception's own `.fix`
            # (ArenaError, shal's own errors) when it has one, else a
            # pointer at the traceback this also prints to stderr.
            traceback.print_exc()
            fix = getattr(e, "fix", None) or (
                "see the traceback on stderr; this is a bug in the story "
                "itself, not something a retry fixes")
            result = {"crashed": f"{type(e).__name__}: {e}", "fix": fix}
            step_ok = False
        overall_ok = overall_ok and step_ok
        if not json_mode:
            print(f"  -> {_plain_outcome(result)}")
        steps.append({"step": step_id, "line": line, "result": result})

    if not json_mode:
        print(f"Run state (including the result card) is under {state_dir}")

    # issue #410: the record and result-card files, as absolute paths, for
    # an agent to open directly -- set only once arena_result_card ran
    # (ctx["record_path"]) and the card step itself (its own result).
    card_path = next((s["result"].get("card_path") for s in steps
                      if s["step"] == "arena_result_card"), None)
    record_path = ctx.get("record_path")

    if json_mode:
        print(json.dumps({"ok": overall_ok, "note": FIRST_LINE, "state_dir": str(state_dir),
                         "record_path": record_path, "card_path": card_path,
                         "steps": steps}, indent=2, default=str))
    elif record_path or card_path:
        print(f"record: {record_path}")
        print(f"result card: {card_path}")
    return 0 if overall_ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pause", type=float, default=2.0,
                        help="seconds to pause before each step (default: 2.0; use 0 for CI)")
    parser.add_argument("--json", action="store_true",
                        help="print one JSON document on stdout instead of narrating")
    args = parser.parse_args(argv)
    return run_story(pause=args.pause, json_mode=args.json)


if __name__ == "__main__":
    sys.exit(main())
