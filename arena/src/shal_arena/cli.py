"""``shal-arena`` — the CLI front door for SHAL Arena (issue #310; the
``check-driver`` command is issue #311's Agent path; ``measure`` is #312's;
``drive`` is #313's; ``bench`` is #314's).

    shal-arena run tasks/rail-3v3.yaml --json
    shal-arena check-driver <run-id> psu0 ./driver.py --json
    shal-arena measure <run-id> dmm0 ./driver.py --json
    shal-arena drive <run-id> psu0 6.5 --json
    shal-arena answer <run-id> low_voltage --json
    shal-arena bench --runs 10 --json
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
from pathlib import Path

from .bench import (
    DEFAULT_POLICY,
    DEFAULT_TASK,
    _default_dmm_driver_registered,
    import_policy,
    run_benchmark,
)
from .errors import ArenaError
from .replay.card import build_result_card
from .replay.rack import build_setup_yaml, render_rack_page
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
        task = args.task or str(DEFAULT_TASK)
        if args.policy:
            policy = import_policy(args.policy)
            result = run_benchmark(task, play_with_shal=policy.play_with_shal,
                                   play_without_shal=policy.play_without_shal,
                                   runs=args.runs, seed_base=args.seed, state_dir=args.state_dir)
        else:
            # issue #397 CTO review: the built-in policy's own dmm driver is
            # registered only for the duration of this call, never when a
            # --policy was given, so it can never collide with one.
            with _default_dmm_driver_registered():
                result = run_benchmark(task, play_with_shal=DEFAULT_POLICY.play_with_shal,
                                       play_without_shal=DEFAULT_POLICY.play_without_shal,
                                       runs=args.runs, seed_base=args.seed,
                                       state_dir=args.state_dir)
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


def _cmd_replay(args: argparse.Namespace) -> int:
    try:
        card_html = build_result_card(args.run_id, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    out_path = Path(args.out) if args.out else Path(args.state_dir) / f"{args.run_id}.card.html"
    out_path.write_text(card_html, encoding="utf-8")
    if args.json:
        _json_out({"ok": True, "side_effect": "write", "run_id": args.run_id,
                  "card_path": str(out_path)})
    else:
        print(f"wrote {out_path}")
    return 0


def _cmd_rack(args: argparse.Namespace) -> int:
    out_path = Path(args.out)
    out_path.write_text(render_rack_page(), encoding="utf-8")
    if args.json:
        _json_out({"ok": True, "side_effect": "write", "rack_path": str(out_path)})
    else:
        print(f"wrote {out_path}")
    return 0


def _cmd_setup_yaml(args: argparse.Namespace) -> int:
    try:
        doc = build_setup_yaml(args.cases)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.out:
        Path(args.out).write_text(doc, encoding="utf-8")
    if args.json:
        _json_out({"ok": True, "side_effect": "write" if args.out else "none",
                  "setup_yaml": doc})
    else:
        print(doc, end="")
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
        help="apply a voltage to the card input this instrument drives (issue #313 Agent "
             "path); the gate refuses a voltage that would damage the card (issue #330)")
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
    p_bench.add_argument("task", nargs="?", default=None,
                         help="path to the task.yaml (default: the built-in sample task, "
                              "tasks/rail-3v3.yaml — only meaningful without --policy too)")
    p_bench.add_argument("--runs", type=int, default=10,
                         help="runs per side, >= 10 (default: 10)")
    p_bench.add_argument("--seed", type=int, default=0,
                         help="base seed; run i on each side uses seed + i (default: 0)")
    p_bench.add_argument("--policy", metavar="PY_FILE",
                         help="a Python file defining play_with_shal(task_path, seed, "
                              "state_dir) and play_without_shal(...), each returning "
                              "(run_id, the dict `answer` returned) — your own agent or a "
                              "scripted one; shal-arena plays no model of its own. Without "
                              "this, bench plays its own built-in default policy, which only "
                              "knows the built-in sample task above")
    add_common(p_bench)
    p_bench.set_defaults(func=_cmd_bench)

    p_replay = sub.add_parser(
        "replay",
        help="write the offline result card for a closed run (issue #315 Agent path)")
    p_replay.add_argument("run_id")
    p_replay.add_argument("--out", default=None, metavar="PATH",
                          help="where to write the card html (default: "
                               "<state-dir>/<run_id>.card.html)")
    add_common(p_replay)
    p_replay.set_defaults(func=_cmd_replay)

    p_rack = sub.add_parser(
        "rack", help="write the rack page: drag instrument tiles to build a setup.yaml")
    p_rack.add_argument("--out", default="rack.html", metavar="PATH")
    add_common(p_rack)
    p_rack.set_defaults(func=_cmd_rack)

    p_setup = sub.add_parser(
        "setup-yaml",
        help="the rack page's own mechanism, without the page: build a setup.yaml "
             "from one or more case names")
    p_setup.add_argument("cases", nargs="+", help="case names, e.g. scpi-psu dmm")
    p_setup.add_argument("--out", default=None, metavar="PATH",
                         help="also write the result to this path")
    add_common(p_setup)
    p_setup.set_defaults(func=_cmd_setup_yaml)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
