"""``shal-arena`` — the CLI front door for SHAL Arena (issue #310; the
``check-driver`` command is issue #311's Agent path; ``measure`` is #312's;
``drive`` is #313's; ``bench`` is #314's).

    shal-arena run tasks/rail-3v3.yaml --json
    shal-arena check-driver <run-id> psu0 ./driver.py --json
    shal-arena measure <run-id> dmm0 ./driver.py --json
    shal-arena drive <run-id> psu0 6.5 --json
    shal-arena answer <run-id> low_voltage --json
    shal-arena bench tasks/rail-3v3.yaml --runs 10 --policy ./policy.py --json

Non-interactive by design: no prompt ever blocks a command, so an agent can
drive the whole challenge headlessly. Every command prints one JSON document
with ``--json``; on an error stdout holds
``{"ok": false, "error": {"type", "message", "fix"}}`` (the same shape
`shal`'s own CLI uses, AGENTS.md) and the message is also on stderr. ``fix``
is never empty.
"""
from __future__ import annotations

import argparse
import json
import sys

from .bench import import_policy, run_benchmark
from .errors import ArenaError
from .runner import answer as _answer
from .runner import check_instrument_driver, drive_input, start_run, take_measurement


def _json_out(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _error_obj(err: ArenaError) -> dict:
    return err.to_dict()


def _report_error(err: ArenaError, as_json: bool) -> int:
    print(f"shal-arena: {err.message}", file=sys.stderr)
    if as_json:
        _json_out({"ok": False, "error": _error_obj(err)})
    else:
        print(f"fix: {err.fix}", file=sys.stderr)
    return err.exit_code


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        result = start_run(args.task, seed=args.seed, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print(f"run {result['run_id']}: {result['task']['title']}")
        print(result['task']['question'])
        for inst in result["instruments"]:
            wiring = inst.get("drives") or inst.get("probe")
            print(f"  {inst['address']} ({inst['case']}) -> {wiring}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    try:
        result = check_instrument_driver(args.run_id, args.instrument, args.manifest,
                                         state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        tile = "LIT" if result["passed"] else "not lit"
        print(f"{args.instrument}: tile {tile}")
        for p in result["problems"]:
            print(f"  PROBLEM  {p}")
    return 0 if result["passed"] else 1


def _cmd_measure(args: argparse.Namespace) -> int:
    try:
        result = take_measurement(args.run_id, args.instrument, args.manifest,
                                  state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print(f"{args.instrument}: {result['op']} -> {result['reading']}")
    return 0


def _cmd_drive(args: argparse.Namespace) -> int:
    try:
        result = drive_input(args.run_id, args.instrument, args.volts, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print(f"{args.instrument}: {result['input']} -> {result['volts']} V; card is "
              f"{result['state']}")
        if result.get("rejected"):
            print(f"  rejected: {result['rejected']}")
    return 0


def _cmd_answer(args: argparse.Namespace) -> int:
    try:
        result = _answer(args.run_id, args.value, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print("correct" if result["correct"] else f"incorrect (was {result['fault_id']})")
        if result["disqualified"]:
            print("disqualified: no measurement logged for this run")
        print(f"sim log: {result['sim_log']}")
    return 0


def _cmd_bench(args: argparse.Namespace) -> int:
    try:
        policy = import_policy(args.policy)
        result = run_benchmark(args.task, play_with_shal=policy.play_with_shal,
                               play_without_shal=policy.play_without_shal,
                               runs=args.runs, seed_base=args.seed, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        for side in ("with_shal", "without_shal"):
            s = result[side]
            print(f"{side}: {s['runs']} runs, median turns {s['median_turns']}, "
                  f"turns range {s['turns_range']}, correct {s['correct']}/{s['runs']}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="shal-arena",
                                     description="SHAL Arena: run a task, check a driver, "
                                                  "answer the question.")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--json", action="store_true", help="print one JSON document")
        p.add_argument("--state-dir", default=".shal-arena", metavar="DIR",
                       help="where run state lives (default: ./.shal-arena)")

    p_run = sub.add_parser("run", help="start a run from a task.yaml")
    p_run.add_argument("task", help="path to the task.yaml")
    p_run.add_argument("--seed", type=int, default=None,
                       help="override the task's seed (a weekly challenge passes this)")
    add_common(p_run)
    p_run.set_defaults(func=_cmd_run)

    p_check = sub.add_parser(
        "check-driver", aliases=["check"],
        help="ADK-style driver check: lights a tile on pass (issue #311 Agent path)")
    p_check.add_argument("run_id")
    p_check.add_argument("instrument", help="the instrument address from `run`'s output")
    p_check.add_argument("manifest", help="path to your driver.py")
    add_common(p_check)
    p_check.set_defaults(func=_cmd_check)

    p_measure = sub.add_parser(
        "measure",
        help="take your own reading on an instrument (issue #312 Agent path)")
    p_measure.add_argument("run_id")
    p_measure.add_argument("instrument", help="the instrument address from `run`'s output")
    p_measure.add_argument("manifest", help="path to your driver.py")
    add_common(p_measure)
    p_measure.set_defaults(func=_cmd_measure)

    p_drive = sub.add_parser(
        "drive",
        help="apply a voltage to the card input this instrument drives (issue #313 Agent path)")
    p_drive.add_argument("run_id")
    p_drive.add_argument("instrument", help="the instrument address from `run`'s output")
    p_drive.add_argument("volts", type=float)
    add_common(p_drive)
    p_drive.set_defaults(func=_cmd_drive)

    p_answer = sub.add_parser("answer", help="answer the question and close the run")
    p_answer.add_argument("run_id")
    p_answer.add_argument("value", help="your answer (one of the card's fault ids, or 'ok')")
    add_common(p_answer)
    p_answer.set_defaults(func=_cmd_answer)

    p_bench = sub.add_parser(
        "bench",
        help="benchmark mode: the same task with SHAL and without it (issue #314 Agent path)")
    p_bench.add_argument("task", help="path to the task.yaml")
    p_bench.add_argument("--runs", type=int, default=10,
                         help="runs per side, >= 10 (default: 10)")
    p_bench.add_argument("--seed", type=int, default=0,
                         help="base seed; run i on each side uses seed + i (default: 0)")
    p_bench.add_argument("--policy", required=True, metavar="PY_FILE",
                         help="a Python file defining play_with_shal(task_path, seed, "
                              "state_dir) and play_without_shal(...), each returning "
                              "(run_id, the dict `answer` returned) — your own agent or a "
                              "scripted one; shal-arena plays no model of its own")
    add_common(p_bench)
    p_bench.set_defaults(func=_cmd_bench)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
