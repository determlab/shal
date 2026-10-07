"""``shal-arena`` — the CLI front door for SHAL Arena (issue #310; the
``check-driver`` command is issue #311's Agent path; ``measure`` is #312's;
``drive`` is #313's; ``bench`` is #314's).

    shal-arena demo --json
    shal-arena tasks --json
    shal-arena run rail-3v3 --json
    shal-arena check-driver <run-id> psu0 ./driver.py --json
    shal-arena measure <run-id> dmm0 ./driver.py --json
    shal-arena drive <run-id> psu0 6.5 --json
    shal-arena call <run-id> relay0 ./driver.py set_relay 0 false --json
    shal-arena answer <run-id> low_voltage --json
    shal-arena bench --runs 10 --json
    shal-arena bench rail-3v3 --runs 10 --policy ./policy.py --json

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
import re
import sys
import time
from pathlib import Path
from typing import Any

from shal.log import redact_secret_args, redact_structured, redact_url_in_text

from .bench import (
    DEFAULT_POLICY,
    DEFAULT_TASK,
    _default_dmm_driver_registered,
    import_policy,
    run_benchmark,
)
from .errors import ArenaError
from .loader import list_tasks, resolve_task
from .replay.card import build_result_card
from .replay.rack import build_setup_yaml, render_rack_page
from .runner import answer as _answer
from .runner import call_op, check_instrument_driver, drive_input, start_run, take_measurement
from .store import DEFAULT_STATE_DIR, RunStore


def _cmd_demo(args: argparse.Namespace) -> int:
    from .demo import run_story

    pause = args.pause if args.pause is not None else (0.0 if args.json else 2.0)
    return run_story(pause=pause, json_mode=args.json)


def _parse_driver_args(specs: list[str]) -> dict[str, dict[str, Any]]:
    """`--driver NAME=PATH` (issue #406 follow-up) -> `{name: {lines, code}}`,
    read once at CLI time -- never a run's own file, so this lives here, not
    in `ui/data.py` (whose one rule is "only this run's own files")."""
    drivers: dict[str, dict[str, Any]] = {}
    for spec in specs:
        name, sep, path = spec.partition("=")
        if not sep:
            raise ArenaError(f"--driver {spec!r} is not NAME=PATH",
                             fix="pass --driver psu=psu_driver.py (one '=', the name first)")
        code = Path(path).read_text(encoding="utf-8")
        drivers[name] = {"lines": len(code.splitlines()), "code": code}
    return drivers


def _cmd_ui(args: argparse.Namespace) -> int:
    try:
        drivers = _parse_driver_args(args.driver)
    except (ArenaError, OSError) as e:
        if isinstance(e, ArenaError):
            return _report_error(e, as_json=True)
        return _report_error(
            ArenaError(f"--driver: {e}", fix="check the path after '=' exists"), as_json=True)

    if args.export:
        if args.run is None:
            return _report_error(
                ArenaError("--export needs --run RUN_ID", fix="pass --run <id> with --export"),
                as_json=True)
        from .ui.export import build_export
        try:
            html = build_export(args.run, state_dir=args.state_dir, agent=args.agent,
                                drivers=drivers)
        except ArenaError as e:
            return _report_error(e, as_json=True)
        Path(args.export).write_text(html, encoding="utf-8")
        print(f"shal-arena ui: wrote {args.export}")
        return 0

    from .ui.server import serve
    try:
        httpd = serve(args.run, state_dir=args.state_dir, port=args.port,
                      open_browser=not args.no_open, drivers=drivers)
    except ArenaError as e:
        return _report_error(e, as_json=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


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


def _cmd_tasks(args: argparse.Namespace) -> int:
    tasks = list_tasks()
    if args.json:
        _json_out({"ok": True, "side_effect": "none", "tasks": tasks})
    else:
        for t in tasks:
            print(f"{t['name']}  ({t['level']})  {t['path']}")
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        result = start_run(str(resolve_task(args.task)), seed=args.seed, state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print(f"run {result['run_id']}: {result['task']['title']}")
        print(result['task']['question'])
        for inst in result["instruments"]:
            wiring = inst.get("drives") or inst.get("switches") or inst.get("probe")
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


def _cmd_call(args: argparse.Namespace) -> int:
    try:
        result = call_op(args.run_id, args.instrument, args.manifest, args.op, args.args,
                         state_dir=args.state_dir)
    except ArenaError as e:
        return _report_error(e, args.json)
    if args.json:
        _json_out(result)
    else:
        print(f"{args.instrument}: {args.op}({', '.join(args.args)}) -> "
              f"{result.get('result')!r}")
        if result.get("rejected"):
            print(f"  rejected: {result['rejected']}")
    return 0 if result["ok"] else (2 if result.get("rejected") else 1)


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
        task = str(resolve_task(args.task)) if args.task else str(DEFAULT_TASK)
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
        # issue #470: the agent's own claim, saved in <run>.cli.jsonl
        # alongside this call, never mixed into this command's own result
        # -- `main()` checks the 2000-char limit before `func` runs.
        p.add_argument("--note", default=None, metavar="TEXT",
                       help=f"a short note in your own words, saved in <run>.cli.jsonl "
                            f"next to this call (max {NOTE_MAX_CHARS} characters); never "
                            "changes what this command does or returns")

    p_demo = sub.add_parser(
        "demo",
        help="run the whole demo story from the installed package, no clone needed "
             "(issue #343/#410 Agent path)")
    p_demo.add_argument("--pause", type=float, default=None,
                        help="seconds between steps (default: 2.0, or 0 under --json)")
    p_demo.add_argument("--json", action="store_true",
                        help="print one JSON document on stdout instead of narrating")
    p_demo.set_defaults(func=_cmd_demo)

    p_ui = sub.add_parser(
        "ui", help="WATCH a run live, PLAY one with buttons, or export a finished one "
                   "(issues #406, #407)")
    p_ui.add_argument("--run", default=None, metavar="RUN_ID",
                      help="the run_id to watch (from `run`'s own output) -- required for "
                           "--export; without it (and without --export), the page starts "
                           "in PLAY mode with no run open yet (issue #407)")
    p_ui.add_argument("--port", type=int, default=0,
                      help="local port (default: 0, picks a free one)")
    p_ui.add_argument("--no-open", action="store_true",
                      help="do not open the browser automatically")
    p_ui.add_argument("--export", default=None, metavar="PATH",
                      help="write one self-contained replay HTML for a FINISHED run to "
                           "PATH instead of serving a live page")
    p_ui.add_argument("--agent", default=None, metavar="LABEL",
                      help="--export only: the agent/session label the replay badge shows")
    p_ui.add_argument("--driver", action="append", default=[], metavar="NAME=PATH",
                      help="a driver.py the agent wrote, shown folded ('The driver the agent "
                           "wrote for the NAME, N lines'); repeatable, e.g. "
                           "--driver psu=psu_driver.py --driver dmm=dmm_driver.py")
    p_ui.add_argument("--state-dir", default=".shal-arena", metavar="DIR",
                      help="where run state lives (default: ./.shal-arena)")
    p_ui.set_defaults(func=_cmd_ui)

    p_tasks = sub.add_parser(
        "tasks", help="list the packaged tasks (name, path, level) that `run` takes by name")
    p_tasks.add_argument("--json", action="store_true", help="print one JSON document")
    p_tasks.set_defaults(func=_cmd_tasks)

    p_run = sub.add_parser("run", help="start a run from a packaged task name or a task.yaml")
    p_run.add_argument("task", help="a packaged task name (see `shal-arena tasks --json`: "
                                    "easy, medium, hard, rail-3v3, relay-rail) or a path to "
                                    "a task.yaml")
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

    p_call = sub.add_parser(
        "call",
        help="run any op of your own driver through SHAL (gate, limits and approval "
             "as usual; issue relay-rail Agent path) -- e.g. a relay's coil ops")
    p_call.add_argument("run_id")
    p_call.add_argument("instrument", help="the instrument address from `run`'s output")
    p_call.add_argument("manifest", help="path to your driver.py")
    p_call.add_argument("op", help="the op name on your driver, e.g. set_relay")
    p_call.add_argument("args", nargs="*", help="positional arguments for the op, e.g. 0 false")
    add_common(p_call)
    p_call.set_defaults(func=_cmd_call)

    p_answer = sub.add_parser("answer", help="answer the question and close the run")
    p_answer.add_argument("run_id")
    p_answer.add_argument("value", help="your answer (one of the card's fault ids, or 'ok')")
    add_common(p_answer)
    p_answer.set_defaults(func=_cmd_answer)

    p_bench = sub.add_parser(
        "bench",
        help="benchmark mode: the same task with SHAL and without it (issue #314 Agent path)")
    p_bench.add_argument("task", nargs="?", default=None,
                         help="a packaged task name or a path to a task.yaml (default: "
                              "the built-in sample task, rail-3v3 — only meaningful "
                              "without --policy too)")
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


_RUN_LINE_RE = re.compile(r"^run (\S+):")

#: issue #470: the one limit on --note, named in both --help and the error
#: a note over it gets.
NOTE_MAX_CHARS = 2000


def _note_too_long_error(note: str) -> ArenaError:
    return ArenaError(
        f"--note is {len(note)} characters, over the {NOTE_MAX_CHARS}-character limit",
        fix=f"shorten the note to {NOTE_MAX_CHARS} characters or fewer")


class _Tee:
    """Mirrors every write to the real stdout, flushed through immediately,
    and keeps a copy for the cli log (#460 round 2 must-fix: `redirect_stdout`
    held back ALL of a command's output until it returned -- `ui --no-open`
    serves silently forever with its own `watching ...` URL line never
    reaching anyone, `demo`'s paced narration became one silent wait then a
    single dump, and an uncaught exception lost everything already
    printed). A command that can never name a run (`ui`'s own commands are
    not among #460's "write one line" set anyway) still streams normally;
    the mirrored copy is only ever used if the command turns out loggable."""

    def __init__(self, real) -> None:
        self._real = real
        self._parts: list[str] = []

    def write(self, s: str) -> int:
        self._parts.append(s)
        n = self._real.write(s)
        self._real.flush()
        return n

    def flush(self) -> None:
        self._real.flush()

    def getvalue(self) -> str:
        return "".join(self._parts)


def _redact_cli_line(argv: list[str], payload: dict[str, Any] | None, text: str | None,
                     note: str | None = None
                     ) -> tuple[list[str], dict[str, Any] | None, str | None, str | None]:
    """issue #460: the one place every value written to ``<run>.cli.jsonl``
    passes through before it reaches disk -- a one-line swap if the
    redaction policy ever changes. `redact_secret_args`/`redact_url_in_text`/
    `redact_structured` (`shal.log`) are the exact same shared rule #457's
    bus exchange hook uses -- `redact_url_in_text`, not `redact_url`, because
    an argv element or a printed line is free text that may merely CONTAIN a
    URL, not a value that IS one end to end (#457 round 2: the same mistake
    mangled unrelated `@`/`:` text and could leave a URL's userinfo in
    place).

    `text` gets the URL rule only, never the key-based secret rule
    `redact_structured` also applies to `json` (round 2 nit): `text` is a
    command's own PRINTED output, which has no keys to check -- the URL
    rule is the only one that ever applied to it.

    `note` (#470) is free text too, so it gets the same URL rule as `text`;
    it also gets `redact_secret_args`' flag-style rule (split on whitespace,
    re-joined) so a note that echoes a command line, e.g. "... --token abc",
    redacts the same way argv itself would -- no second redaction path."""
    argv = [redact_url_in_text(a) for a in redact_secret_args(argv)]
    if payload is not None:
        payload = redact_structured(payload)
    if text is not None:
        text = redact_url_in_text(text)
    if note is not None:
        note = redact_url_in_text(" ".join(redact_secret_args(note.split())))
    return argv, payload, text, note


def _extract_run_id(args: argparse.Namespace, printed: str,
                    parsed: dict[str, Any] | None) -> str | None:
    """issue #460: most commands take a run id directly (`run_id`/`--run`
    dest `run`); `run` itself creates one, known only once it has printed
    (`--json`'s own `run_id` key, or the plain-text `run <id>: ...` line).
    `tasks`/`--help`/a `run` that raised before an id ever existed write
    nothing -- none of the three matches below fire for them."""
    run_id = getattr(args, "run_id", None) or getattr(args, "run", None)
    if run_id:
        return run_id
    if isinstance(parsed, dict) and isinstance(parsed.get("run_id"), str):
        return parsed["run_id"]
    m = _RUN_LINE_RE.match(printed)
    return m.group(1) if m else None


def _log_cli_call(state_dir: str, run_id: str, argv: list[str], exit_code: int,
                  parsed: dict[str, Any] | None, printed: str,
                  note: str | None = None) -> None:
    """issue #460: one JSON line to ``<run>.cli.jsonl``, under the run's own
    lock (`RunStore.append_cli_log`, reusing #436/#442's lock -- agents run
    commands in parallel). `json` is the parsed `--json` output; `text` is
    the printed text when the command ran without `--json` (``json: null``
    then, so the window still shows the answer).

    Intentional (round 2 nit): a command's own error message goes to
    stderr, not stdout (`_report_error`), so a FAILING command run without
    `--json` logs `text: ""` here -- only `exit_code` and `argv` say
    anything went wrong. stderr is never mirrored into this file; `--json`
    is the one shape that carries the error text itself, in `json.error`.

    `note` (#470) is the agent's own claim about this call, saved alongside
    it -- never folded into `json`/`text`, which are only ever this
    command's own output."""
    text = None if parsed is not None else printed
    argv, parsed, text, note = _redact_cli_line(argv, parsed, text, note)
    entry = {"time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "argv": argv, "exit_code": exit_code, "json": parsed, "text": text,
             "note": note}
    RunStore(state_dir).append_cli_log(run_id, entry)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    raw_argv = list(argv if argv is not None else sys.argv[1:])

    # issue #470: checked before the command runs, so an over-limit note
    # never runs the command and never writes a cli.jsonl line for it.
    note = getattr(args, "note", None)
    if note is not None and len(note) > NOTE_MAX_CHARS:
        return _report_error(_note_too_long_error(note), getattr(args, "json", False))

    # issue #460: mirror what the command prints, to log it, without ever
    # holding it back from the real stdout (round 2 must-fix: a buffer-then-
    # replay here made `ui`'s live server line, and `demo`'s paced
    # narration, silent until the command returned).
    real_stdout = sys.stdout
    tee = _Tee(real_stdout)
    sys.stdout = tee
    try:
        exit_code = args.func(args)
    finally:
        sys.stdout = real_stdout
    printed = tee.getvalue()

    parsed: dict[str, Any] | None = None
    if getattr(args, "json", False):
        try:
            candidate = json.loads(printed)
        except json.JSONDecodeError:
            candidate = None
        if isinstance(candidate, dict):
            parsed = candidate

    run_id = _extract_run_id(args, printed, parsed)
    if run_id is not None:
        state_dir = getattr(args, "state_dir", DEFAULT_STATE_DIR)
        _log_cli_call(state_dir, run_id, raw_argv, exit_code, parsed, printed, note)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
