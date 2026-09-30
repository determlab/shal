"""``shal`` — the base command-line front door (issue #54).

SHAL stands on its own *without* MCP: this CLI is the primary way a human (or a
shell agent) drives a topology. MCP is one subcommand here (``shal mcp``), an
adapter — not the front door.

    shal probe lab.yaml                  # one-shot: print device state and exit
    shal probe lab.yaml dev__get_state   # read one named tool
    shal tools lab.yaml                  # list the device tools (read / gated)
    shal call lab.yaml dev read_celsius --json   # run one op; a gated op is refused (exit 2)
    shal call lab.yaml board hostname --via ssh  # pin one route of a node with routes
    shal routes lab.yaml board --json    # the routes a node declares, in order
    shal mcp   lab.yaml                  # serve to an MCP host (the adapter)
    shal probe lab.yaml --drivers ./drivers/   # load local/unpackaged drivers
    shal check shal,sim-sensor --json    # driver conformance as a JSON report
    shal check driver:MyThing --topology sim.yaml   # a local class + live sim probes
    shal records --verdict fail --json   # read the record store (read-only, no topology)

The legacy ``shal-mcp`` command still works (it is ``shal mcp``).

These commands are thin views over the same core (``shal.load`` → ``Bridge``);
the read/serve/driver-loading logic is shared with ``shal.mcp.server`` so there
is one implementation, not two.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import re
import sys


def _use_selector_loop_on_win32() -> None:
    """Windows: give the *command* a SelectorEventLoop by default (issue #94).

    A driver that wraps an `aiomqtt`-style library runs its own asyncio loop; on
    win32 the default `ProactorEventLoop` has no `add_reader`, so that loop dies
    with `NotImplementedError`. `shal mcp` set this for itself (#87) but `shal
    probe` did not — the same driver served over MCP and failed under the CLI a
    cold user is told to try first.

    It belongs here, once per command, and **never at import time**: `shal` is a
    library, and silently swapping the event-loop policy of a process that merely
    imported it is the same overreach as the locked non-negotiable "the library
    never configures logging" (`docs/agents/context.md`) — apps choose global
    state, libraries don't. `shal.mcp.server.main` (the legacy `shal-mcp` console
    script, which does not come through `main` here) calls this same helper, so
    there is one implementation of the choice, not two.
    """
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _add_drivers_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--drivers", action="append", default=[], metavar="PATH",
                   help="import local driver module(s) before loading — a .py file "
                        "or a directory of them (repeatable).")


# `--json` on probe / tools / docs --list (shal#185) and docs --samples (#206). One
# JSON document on stdout, the same exit code as without --json, and every message
# still on stderr (as `shal call --json` does). An error is
# `{"ok": false, "error": {"type": <short name>, "message": <text>, "fix": <what fixes it>}}`
# (#279) — the one shape for every command; `fix` is never empty.
def _json_out(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _error_obj(type_: str, message: str, fix: str) -> dict:
    return {"type": type_, "message": message, "fix": fix or "run the command with --help"}


def _help_fix(cmd: str) -> str:
    return f"run `shal {cmd} --help` for the arguments"


_READ_FIX = "check the device and its wiring, then run `shal probe --help`"


def _load_fault(msg: str, cmd: str) -> dict:
    """The error object for a topology/driver load failure reported as a message."""
    if "no driver installed" in msg:
        from .registry import ENTRY_POINT_GROUP
        return _error_obj(
            "NoDriver", msg,
            f"install a package that exposes the driver via the '{ENTRY_POINT_GROUP}' "
            "entry point, or register a local driver file with --drivers <file.py | "
            "directory/>; `shal docs` is the guide to write one")
    if "topology file not found" in msg:
        return _error_obj("TopologyNotFound", msg,
                          "pass the path to a topology YAML, or run "
                          "`shal docs --sample hello --to DIR` for a ready-to-edit one")
    if "failed importing driver" in msg or "--drivers" in msg:
        return _error_obj("DriverImport", msg,
                          "fix the driver file named in the message, or check the "
                          "--drivers path")
    return _error_obj("LoadError", msg,
                      "fix the topology file, or run `shal docs --sample hello --to DIR` "
                      f"for a working one; `shal {cmd} --help` shows the arguments")


def _json_error(error: dict, code: int = 1) -> int:
    print(error["message"], file=sys.stderr)
    _json_out({"ok": False, "error": error})
    return code


def _json_load(args, cmd: str):
    """Load the topology for a `--json` command: the Hal, or the exit code after the
    error was reported. The shared loaders exit with a message (exit 1); any other
    load failure is exit 1 too, as its traceback is without --json."""
    from .mcp.server import _import_drivers, _resolve_hal
    try:
        _import_drivers(args.drivers)
        return _resolve_hal(args.topology, one_line=False)
    except SystemExit as e:
        if not isinstance(e.code, str):
            raise
        return _json_error(_load_fault(e.code, cmd))
    except Exception as e:  # noqa: BLE001 - a bad topology is a JSON error, exit 1
        return _json_error(_load_fault(f"shal {cmd}: cannot load {args.topology}: "
                                       f"{type(e).__name__}: {e}", cmd))


# run_with is built from an ALLOW-list, never a deny-list: a token pastes into bash,
# PowerShell and cmd only if every character is one we know is literal there.
# Plain (bare) tokens: ASCII letters, digits and _ - . / : \ + — none of the three
# shells gives any of these a meaning in a bare word (bash drops a \ inside a word
# but never runs anything because of it).
_PLAIN_TOKEN = re.compile(r"[A-Za-z0-9_\-./:\\+]+\Z")
# Quoted tokens add only a space and # = @ ~ , each literal inside "..." in all
# three: bash expands only $ ` \ ! there, PowerShell $ ` and the curly quotes it
# reads as ", cmd % ! and " itself. Nothing non-ASCII, no control character.
_QUOTABLE_TOKEN = re.compile(r"[A-Za-z0-9_\-./:\\+ #=@~]+\Z")
_PARAM_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _shell_token(token: str) -> str | None:
    """``token`` as it can be pasted into bash, PowerShell and cmd alike: bare when
    it is plain, else in double quotes (the quoting all three share). None when
    neither is safe: a character outside the allow-lists, or a trailing \\ (bare,
    bash reads it as escaping the next space; quoted, it escapes the closing ")."""
    if token.endswith("\\") or token == "--%":  # --% stops PowerShell's parser
        return None
    if _PLAIN_TOKEN.match(token):
        return token
    if _QUOTABLE_TOKEN.match(token):
        return f'"{token}"'
    return None


def _call_command(args, fact: dict, schema: dict) -> str | None:
    """The `shal call` line that runs one op: its device and op from the catalog,
    and a ``name=<name>`` placeholder per required value (by name, so the order of
    the op's parameters does not matter). None when a path in it cannot be quoted
    for every shell (see `_shell_token`)."""
    def path(p: str) -> str:
        # a path starting with - would read as a flag (#197). Not `--`: PowerShell
        # 5.1 splits a bare -x.yaml into -x .yaml even after it. ./ is safe in all.
        return "./" + p if p.startswith("-") else p
    tokens = [path(args.topology), fact["device"], fact["op"]]
    for d in args.drivers:
        tokens += ["--drivers", path(d)]
    quoted = [_shell_token(t) for t in tokens]
    names = schema.get("required", [])
    if None in quoted or not all(_PARAM_NAME.match(p) for p in names):
        return None
    required = [f"{p}=<{p}>" for p in names]
    return " ".join(["shal call", *quoted[:3], *required, *quoted[3:]])


def _probe_json(args) -> int:
    """`shal probe --json`: the reads it ran and the writes it did not run."""
    from .mcp import Bridge
    from .mcp.server import _probe_pick, _probe_split
    hal = _json_load(args, "probe")
    if isinstance(hal, int):
        return hal
    try:
        bridge = Bridge(hal)
        defs = bridge.tool_defs()
        facts = {t["name"]: t for t in hal.tool_catalog()}
        try:
            picked = [_probe_pick(defs, args.tool)] if args.tool else None
        except SystemExit as e:
            return _json_error(_error_obj("BadTool", str(e.code),
                                          "run `shal probe <topology>` with no tool to "
                                          "list the reads, or use `shal call` for a write"))
        reads, writes = _probe_split(defs)
        read_out = []
        for d in picked or reads:
            f = facts[d["name"]]
            entry = {"tool": d["name"], "device": f["device"], "op": f["op"]}
            try:
                out = bridge.call(d["name"], {})
            except Exception as e:  # noqa: BLE001 - as the text snapshot: one bad read
                if picked:  # a named read that raises exits 1 without --json too
                    return _json_error(_error_obj(
                        "ReadFailed", f"shal probe: {d['name']} failed: "
                                      f"{type(e).__name__}: {e}",
                        _READ_FIX))
                entry.update(ok=False, error=_error_obj(
                    "ReadFailed", f"{type(e).__name__}: {e}", _READ_FIX))
            else:
                if out.get("ok"):
                    entry.update(ok=True, value=out.get("result"))
                else:
                    entry.update(ok=False, error=_error_obj(
                        "ReadFailed", str(out.get("error", out.get("message"))),
                        out.get("fix") or _READ_FIX))
            entry["unit"] = f.get("unit")
            read_out.append(entry)
        write_out = [{"tool": d["name"], "device": facts[d["name"]]["device"],
                      "op": facts[d["name"]]["op"],
                      "side_effect": facts[d["name"]]["side_effect"],
                      "gated": bool(d["annotations"].get("destructiveHint")),
                      "run_with": _call_command(args, facts[d["name"]],
                                                d["input_schema"])}
                     for d in writes]
    finally:
        hal.close()
    _json_out({"ok": True, "topology": args.topology, "reads": read_out,
               "writes_not_run": write_out})
    return 0


def _cmd_probe(args) -> int:
    if args.json:
        return _probe_json(args)
    from .mcp import Bridge
    from .mcp.server import _import_drivers, _probe, _resolve_hal
    _import_drivers(args.drivers)
    hal = _resolve_hal(args.topology)
    try:
        return _probe(Bridge(hal), args.tool or None)
    finally:
        hal.close()


def _tools_json(args) -> int:
    """`shal tools --json`: every device op, in full (#185)."""
    hal = _json_load(args, "tools")
    if isinstance(hal, int):
        return hal
    try:
        schemas = {d["name"]: d for d in hal.tool_schemas()}
        tools = []
        for f in hal.tool_catalog():
            d = schemas[f["name"]]
            ann = f["annotations"]
            kind = ("read" if ann.get("readOnlyHint")
                    else "gated" if ann.get("destructiveHint") else "write")
            tools.append({"tool": f["name"], "device": f["device"], "op": f["op"],
                          "kind": kind, "side_effect": f["side_effect"],
                          "gated": bool(ann.get("destructiveHint")),
                          "idempotent": f["idempotent"], "unit": f.get("unit"),
                          "description": d["description"],
                          "input_schema": d["input_schema"],
                          # a node with routes: its route names, in order (#237)
                          **({"routes": f["routes"]} if "routes" in f else {})})
    finally:
        hal.close()
    _json_out({"ok": True, "topology": args.topology, "tools": tools})
    return 0


def _cmd_tools(args) -> int:
    if args.json:
        return _tools_json(args)
    from .mcp import Bridge
    from .mcp.server import _import_drivers, _resolve_hal
    _import_drivers(args.drivers)
    hal = _resolve_hal(args.topology)
    try:
        for d in Bridge(hal).tool_defs():
            ann = d.get("annotations") or {}
            kind = ("read" if ann.get("readOnlyHint")
                    else "gated" if ann.get("destructiveHint") else "write")
            print(f"  {d['name']:<28} [{kind:<5}] {d.get('description', '')[:58]}")
    finally:
        hal.close()
    return 0


def _cmd_mcp(args) -> int:
    """Run the MCP server — the adapter. Delegates to shal.mcp.server so there is
    exactly one server implementation."""
    from .mcp import server
    argv: list[str] = []
    if args.topology:
        argv.append(args.topology)
    for d in args.drivers:
        argv += ["--drivers", d]
    argv += ["--approve", args.approve]
    return server.main(argv)


# `shal check` exit codes: 0 = no problems, 1 = the report has problems,
# 2 = the check could not run (bad target, import failure, bad topology) —
# the same 2 argparse uses for a usage error, so 1 always means "fix the driver".
_CHECK_USAGE_ERROR = 2


def _check_fail(msg: str, as_json: bool = False, type_: str = "CheckCouldNotRun",
                fix: str = "") -> int:
    print(f"shal check: {msg}", file=sys.stderr)
    if as_json:
        _json_out({"ok": False, "error": _error_obj(
            type_, msg, fix or _help_fix("check"))})
    return _CHECK_USAGE_ERROR


def _load_check_target(target: str) -> type:
    """``module:Class`` imports the class (cwd first on ``sys.path``, like a local
    ``driver.py``); anything else is a registered ``compatible``. Raises
    ``ValueError`` with a message that says what to change."""
    import importlib

    from . import registry
    from .driver import Driver
    from .errors import LoadError
    if ":" not in target:
        try:
            return registry.resolve(target)
        except LoadError as e:
            raise ValueError(f"{e}\n  - A local class? Name it as module:Class "
                             f"(e.g. driver:MyThing).") from e
    mod_name, _, cls_name = target.partition(":")
    if not mod_name or not cls_name:
        raise ValueError(f"'{target}' is not module:Class (e.g. driver:MyThing)")
    if os.getcwd() not in sys.path:
        sys.path.insert(0, os.getcwd())
    from .driver import _policy_snapshot, _refuse_import_change
    before = _policy_snapshot()
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001 - any import failure is a clean exit 2
        raise ValueError(f"failed importing module '{mod_name}': "
                         f"{type(e).__name__}: {e}") from e
    try:  # driver code never changes the approval policy (ADR-001 addendum 5)
        _refuse_import_change(before, mod_name)
    except LoadError as e:
        raise ValueError(str(e)) from e
    cls = getattr(mod, cls_name, None)
    if cls is None:
        raise ValueError(f"module '{mod_name}' has no attribute '{cls_name}'")
    if not (isinstance(cls, type) and issubclass(cls, Driver)):
        raise ValueError(f"'{target}' is not a shal Driver subclass")
    # An unregistered class is registered in THIS process only, so its catalog
    # entry builds and a --topology naming its compatible binds it — exactly what
    # importing a module that uses @shal.register does. Never shadow another class.
    compatible = getattr(cls, "compatible", "")
    if compatible:
        # load what resolve()/catalog() would see first, or the guard misses a
        # shipped/installed claimant (shal,sim-sensor) and the check silently shadows it
        registry._load_entry_points()
        registry._ensure_bundled()
        claimed = registry._entries.get(compatible) or []
        others = [c for c in claimed if c is not cls]
        if others:
            names = ", ".join(f"{c.__module__}.{c.__qualname__}" for c in others)
            raise ValueError(f"compatible '{compatible}' is already registered by "
                             f"{names} — give {cls_name} its own compatible")
        registry.register(cls)
    return cls


def _cmd_check(args) -> int:
    """A thin CLI over ``conformance.check_driver`` (shal#148, ADK R6)."""
    from .conformance import check_driver
    if args.topology is not None and not os.path.isfile(args.topology):
        return _check_fail(f"topology file not found: {args.topology}", args.json,
                           "TopologyNotFound", "pass the path to an existing topology YAML")
    try:
        cls = _load_check_target(args.target)
    except ValueError as e:
        return _check_fail(str(e), args.json, "BadTarget",
                           "pass module:Class or a registered compatible; "
                           "`shal docs --list` lists the reference drivers")
    try:
        report = check_driver(cls, args.topology)
    except Exception as e:  # noqa: BLE001 - the check could not run: no traceback
        return _check_fail(f"check could not run: {type(e).__name__}: {e}", args.json)
    if args.json:
        print(json.dumps({"compatible": report.compatible, "ok": report.ok,
                          "problems": report.problems, "warnings": report.warnings,
                          "checked": report.checked}, indent=2))
    else:
        print(str(report))
    return 0 if report.ok else 1


# `shal call` exit codes (shal#160, ADK R11). 2 is the gate's: a config/actuator op
# was refused and nothing was sent. So "cannot run" (usage error, unknown node/op,
# bad args, a topology that does not load) is 3, not the 2 argparse and `shal check`
# use — an agent must be able to tell "a person has to approve this" from "I made
# a mistake". 1 means the op ran and failed (a device error, or its declared
# limits rejected the value; the JSON result says which).
_CALL_FAILED = 1
_CALL_REFUSED = 2
_CALL_CANNOT_RUN = 3


class _CallCannotRun(Exception):
    """A `shal call` mistake: printed on stderr, exit 3, never a traceback. An
    unknown route name (#237) also carries the valid ``routes``: with --json they
    are on stdout, so an agent reads the names for its next call."""

    def __init__(self, msg: str, routes: list[str] | None = None) -> None:
        super().__init__(msg)
        self.routes = routes


def _usage_error(parser: argparse.ArgumentParser, cmd: str, code: int = 2,
                 argv: list[str] | None = None):
    """argparse's ``error`` hook for a subcommand with --json. Usage goes to stderr;
    with --json on the command line, stdout also holds ``{"ok": false, "error": ...}``.
    ``code`` is argparse's 2, except for `call`, where 2 means a gate refusal."""
    def error(message: str):
        parser.print_usage(sys.stderr)
        print(f"shal {cmd}: {message}", file=sys.stderr)
        if "--json" in (sys.argv[1:] if argv is None else argv):
            _json_out({"ok": False, "error": _error_obj(
                "UsageError", message, _help_fix(cmd))})
        raise SystemExit(code)
    return error


def _call_usage_error(parser: argparse.ArgumentParser, argv: list[str] | None = None):
    return _usage_error(parser, "call", _CALL_CANNOT_RUN, argv)


def _find_call_tool(hal, node_key: str, op: str) -> tuple[str, object, object]:
    """(tool name, node, op function) for ``node_key``'s op on the agent surface.
    ``node_key`` is the node's id, its /path, or its tool handle (`shal tools`)."""
    idx = hal._tool_index()
    hits = [(name, node) for name, (node, opname) in idx.items()
            if (node_key in (node.id, node.path) or name == f"{node_key}__{opname}")]
    if not hits:
        handles = sorted({n.rsplit("__", 1)[0] for n in idx})
        raise _CallCannotRun(f"no device '{node_key}' on this topology's agent surface "
                             f"(devices: {', '.join(handles) or 'none'})")
    for name, node in hits:
        if idx[name][1] == op:
            return name, node, type(node.driver).capability_ops()[op]
    ops = sorted({idx[name][1] for name, _ in hits})
    raise _CallCannotRun(f"device '{node_key}' has no op '{op}' (ops: {', '.join(ops)})")


def _call_pin(node, via: str) -> dict:
    """The ``via`` argument for ``shal call --via <name>`` (#237): the pin on a node
    with routes; nothing on a node without, whose one route is its main one. Any
    other name is a mistake, refused before any I/O, with the valid names."""
    from .hal import declared_routes, route_names
    names = [r["name"] for r in declared_routes(node)]
    if via not in names:
        raise _CallCannotRun(f"{node.path}: no route named {via!r}; routes: "
                             f"{', '.join(names) or 'none'}", routes=names)
    return {"via": via} if route_names(node) else {}


def _coerce(value: str, prop: dict, pname: str):
    """One command-line string -> the JSON type the op's schema declares
    (`limits.json_type` emits integer, number, boolean or string)."""
    kind = prop.get("type", "string")
    try:
        if kind == "integer":
            return int(value, 0) if value.lower().lstrip("-")[:2] in ("0x", "0o", "0b") \
                else int(value)
        if kind == "number":
            return float(value)
    except ValueError:
        raise _CallCannotRun(f"'{pname}' must be a {kind}, got '{value}'") from None
    if kind == "boolean":
        low = value.lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
        raise _CallCannotRun(f"'{pname}' must be true or false, got '{value}'")
    return value


def _call_arguments(fn, schema: dict, raw: list[str]) -> dict:
    """Map ``shal call`` values onto the op's parameters: positional values in the
    op's parameter order, then ``name=value`` for any parameter by name."""
    import inspect
    import re
    names = [p for p in inspect.signature(fn).parameters if p != "self"]
    listed = ", ".join(names) or "none"
    props = schema.get("properties", {})
    out: dict = {}
    by_name = False
    for tok in raw:
        m = re.match(r"([A-Za-z_]\w*)=(.*)\Z", tok, re.S)
        if m and m.group(1) in names:
            by_name = True
            pname, value = m.group(1), m.group(2)
        elif by_name:
            raise _CallCannotRun(f"'{tok}': a positional value cannot follow "
                                 f"name=value (params: {listed})")
        else:
            free = [n for n in names if n not in out]
            if not free:
                raise _CallCannotRun(f"too many values (params: {listed})")
            pname, value = free[0], tok
        if pname in out:
            raise _CallCannotRun(f"'{pname}' is given twice")
        out[pname] = _coerce(value, props.get(pname, {}), pname)
    missing = [n for n in schema.get("required", []) if n not in out]
    if missing:
        raise _CallCannotRun(f"missing value for {', '.join(missing)} (params: {listed})")
    return out


def _cmd_call(args) -> int:
    """Run one op from the command line (shal#160, ADK R11). A ``none``/``write`` op
    runs; a ``config``/``actuator`` op is refused from its declared label BEFORE it
    is invoked — nothing is sent. The label is read with the wrapper's own
    ``inferred_side_effect`` and the node's ENFORCED gated set (one source of
    truth, D4), and the op that does run runs under ``DenyAll``, so the op-layer
    gate still backs this up: anything that reached it would be denied, never
    prompted or passed. The gated set is the one the wrapper captured at bind
    (issue #114, ADR-001 addendum 5b: the topology's ``policy:`` ∪ the host's
    widenings), not the shipped default, so this refusal and the runtime gate
    cannot disagree. There is no flag: the caller cannot choose its own gate."""
    from .approval import DenyAll, approver
    from .driver import inferred_side_effect
    from .errors import HOW_TO_APPROVE_LINE
    from .hal import _node_gated
    from .mcp.server import _import_drivers, _resolve_hal

    def emit(payload: dict) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))

    if not os.path.isfile(args.topology):
        msg = f"shal call: topology file not found: {args.topology}"
        print(msg, file=sys.stderr)
        emit({"ok": False, "error": _load_fault(msg, "call")})
        return _CALL_CANNOT_RUN
    try:
        _import_drivers(args.drivers)
        hal = _resolve_hal(args.topology)
    except SystemExit as e:  # the shared loaders exit with a message, not a code
        msg = e.code if isinstance(e.code, str) else f"shal call: load failed ({e.code})"
        print(msg, file=sys.stderr)
        emit({"ok": False, "error": _load_fault(msg, "call")})
        return _CALL_CANNOT_RUN
    except Exception as e:  # noqa: BLE001 - a bad topology is a clean exit 3
        msg = f"shal call: cannot load {args.topology}: {type(e).__name__}: {e}"
        print(msg, file=sys.stderr)
        emit({"ok": False, "error": _load_fault(msg, "call")})
        return _CALL_CANNOT_RUN
    try:
        name, node, fn = _find_call_tool(hal, args.node, args.op)
        device = node.id or node.path
        pin = _call_pin(node, args.via) if args.via is not None else {}
        side_effect = inferred_side_effect(fn)
        if side_effect in _node_gated(node):  # from the label: never invoked
            msg = (f"refused: {device}.{args.op} is labelled '{side_effect}'. A "
                   f"'{side_effect}' op needs a person's approval, and shal call "
                   f"cannot give it. Nothing was sent to the device.\n"
                   f"  To run it with approval, either:\n"
                   f"    - serve the topology to an MCP host:  shal mcp {args.topology}\n"
                   f"      (the host shows the call to a person, who approves it), or\n"
                   f"    - in Python:  with shal.approver(<your Approver>): "
                   f"hal.get_device({device!r}).{args.op}(...)\n"
                   f"  There is no --approve flag: the agent that runs a command "
                   f"cannot approve its own call.")
            print(f"shal call: {msg}", file=sys.stderr)
            emit({"ok": False, "rejected": "approval", "tool": name, "device": device,
                  "op": args.op, "side_effect": side_effect, "sent": False,
                  "approve_with": [f"shal mcp {args.topology}",
                                   "with shal.approver(...): in Python"],
                  "how_to_approve": HOW_TO_APPROVE_LINE,
                  "error": _error_obj("ApprovalRequired", msg, HOW_TO_APPROVE_LINE)})
            return _CALL_REFUSED
        schema = next(s["input_schema"] for s in hal.tool_schemas() if s["name"] == name)
        arguments = _call_arguments(fn, schema, args.args)
        with approver(DenyAll()):
            out = hal.call_tool(name, {**arguments, **pin})
    except _CallCannotRun as e:
        print(f"shal call: {e}", file=sys.stderr)
        if e.routes is not None:  # a bad --via: the valid names, for the next call
            emit({"ok": False, "routes": e.routes, "error": _error_obj(
                "BadRoute", str(e), f"pass one of: {', '.join(e.routes) or 'none'} "
                                    f"(`shal routes {args.topology} {args.node}`)")})
        else:
            emit({"ok": False, "error": _error_obj(
                "CannotRun", str(e), f"run `shal tools {args.topology}` for the devices, "
                                     "ops and arguments, or `shal call --help`")})
        return _CALL_CANNOT_RUN
    except Exception as e:  # noqa: BLE001 - the op raised past call_tool: no traceback
        msg = f"{args.node}.{args.op} failed: {type(e).__name__}: {e}"
        print(f"shal call: {msg}", file=sys.stderr)
        emit({"ok": False, "error": _error_obj(
            "OpFailed", msg, "check the device and its wiring; `shal call --help` "
                             "shows the arguments")})
        return _CALL_FAILED
    finally:
        hal.close()
    payload = {"ok": out["ok"], "tool": name, "device": device, "op": args.op,
               "side_effect": side_effect, **{k: v for k, v in out.items() if k != "ok"}}
    if not out["ok"]:
        kind = {"limits": "LimitsRejected", "approval": "ApprovalDenied"}.get(
            out.get("rejected"), "OpFailed")
        payload["error"] = _error_obj(kind, str(out.get("error")), out.get("fix") or (
            "pass a value inside the op's declared limits (`shal tools "
            f"{args.topology} --json`)" if kind == "LimitsRejected" else
            "check the device and its wiring; `shal call --help` shows the arguments"))
        print(f"shal call: {device}.{args.op} failed: {out.get('error')}", file=sys.stderr)
        emit(payload)
        return _CALL_FAILED
    if args.json:
        emit(payload)
    else:
        print("ok" if out["result"] is None else json.dumps(out["result"], default=str))
    return 0


# `shal records` fix text is one fixed sentence (issue #251): it names the exact
# command an agent runs next, so it is never reworded per-call the way an error
# message built from the failing value would be.
_NO_STORE_FIX = ("run a test with pytest-shal, or run the jig sample "
                 "('shal docs --sample jig --to DIR', then 'python DIR/run.py' in "
                 "that folder) and read it with 'shal records jig-records'; or "
                 "pass the directory that holds records.db")
_NEWER_RECORD_FIX = "re-run with --skip-newer, or upgrade pyshal"
_BAD_RECORD_FIX = "fix or remove the record file named in the message"


def _cmd_records(args) -> int:
    """`shal records [DIR]` — read `record.write()`'s store (issue #251).

    Read-only (`side_effect: none`): no topology, no driver I/O, just
    `record.read()` on `DIR/records.db` and the `records/` YAML beside it. A
    missing store is a structured `NoStore` error naming the fix; a record
    written by a newer `record_version` refuses the whole read (`record.read()`'s
    own one-sentence message) unless `--skip-newer`, which returns the readable
    records and lists the ones it skipped instead of blocking on them.
    """
    from .record import RecordError, _NewerRecordError, db_path, read

    store = db_path(args.dir)
    if not store.exists():
        msg = f"no records.db in {args.dir}"
        print(f"shal records: {msg}. {_NO_STORE_FIX}", file=sys.stderr)
        if args.json:
            _json_out({"ok": False,
                       "error": {"type": "NoStore", "message": msg, "fix": _NO_STORE_FIX}})
        return 1

    try:
        if args.skip_newer:
            records, skipped = read(args.dir, unit=args.unit, station=args.station,
                                    sequence=args.sequence, verdict=args.verdict,
                                    newer="skip")
        else:
            records = read(args.dir, unit=args.unit, station=args.station,
                           sequence=args.sequence, verdict=args.verdict)
            skipped = []
    except RecordError as e:
        print(f"shal records: {e}", file=sys.stderr)
        if args.json:
            # One error shape for every `--json` failure ({type, message, fix}):
            # a newer record_version names --skip-newer as its fix; any other
            # RecordError (a malformed record, which --skip-newer would not fix)
            # is "BadRecord" instead, never the same fix text.
            if isinstance(e, _NewerRecordError):
                error = {"type": "NewerRecord", "message": str(e), "fix": _NEWER_RECORD_FIX}
            else:
                error = {"type": "BadRecord", "message": str(e), "fix": _BAD_RECORD_FIX}
            _json_out({"ok": False, "error": error})
        return 1

    newest_first = list(reversed(records))
    if args.last is not None:
        newest_first = newest_first[:args.last] if args.last > 0 else []

    if args.json:
        payload = {"ok": True, "store": str(store),
                   "records": [r.to_mapping() for r in newest_first]}
        if args.skip_newer:
            payload["skipped"] = [{"id": rid, "record_version": v} for rid, v in skipped]
        _json_out(payload)
        return 0

    for r in newest_first:
        print(f"{r.started}  {r.unit}  {r.station}  {r.sequence}  {r.verdict}  {r.record}")
    for rid, version in skipped:
        print(f"# skipped (record_version {version}, newer than this reads): {rid}",
              file=sys.stderr)
    return 0


def _find_node(hal, key: str):
    """The device node for ``shal routes``: its id, its /path, or its tool handle
    (`shal tools`). None when there is no such device."""
    if key.startswith("/"):
        node = hal._by_path(key)
    else:
        node = hal._ids.get(key)
    if node is not None and node.driver is not None:
        return node
    return next((n for name, (n, _) in hal._tool_index().items()
                 if name.rsplit("__", 1)[0] == key), None)


def _cmd_routes(args) -> int:
    """``shal routes <topology> <node>`` (#237, RFC-001 §7): the routes the file
    declares for one node, in order — each route's name, the bus it goes via and
    its address. A node without routes shows its one main route. No up/down
    state: v1 shows the declaration only."""
    from .hal import declared_routes
    from .mcp.server import _import_drivers, _resolve_hal
    if args.json:
        hal = _json_load(args, "routes")
        if isinstance(hal, int):
            return hal
    else:
        _import_drivers(args.drivers)
        hal = _resolve_hal(args.topology)
    try:
        node = _find_node(hal, args.node)
        if node is None:
            handles = sorted({n.rsplit("__", 1)[0] for n in hal._tool_index()})
            msg = (f"shal routes: no device '{args.node}' on this topology "
                   f"(devices: {', '.join(handles) or 'none'})")
            if args.json:
                return _json_error(_error_obj(
                    "NoDevice", msg, f"run `shal tools {args.topology}` to list the devices"))
            print(msg, file=sys.stderr)
            return 1
        routes = declared_routes(node)
    finally:
        hal.close()
    if args.json:
        _json_out({"ok": True, "topology": args.topology,
                   "device": node.id or node.path, "path": node.path,
                   "routes": routes})
        return 0
    print(f"{node.id or node.path} ({node.path})")
    for r in routes:
        print(f"  {r['name']:<14} via {r['via']}  address {r['address']}")
    return 0


def _strip_front_matter(text: str) -> str:
    """Drop a leading `---` front-matter block. Both shipped docs carry the repo's
    doc-standard header (type/owner/reviewed) — that is bookkeeping for the repo, not
    part of the contract an agent is meant to read, so `shal docs` never prints it."""
    if not text.startswith("---"):
        return text
    close = text.find("\n---", 3)
    if close == -1:
        return text
    eol = text.find("\n", close + 1)
    return text[eol + 1:].lstrip("\r\n") if eol != -1 else ""


def _references() -> dict[str, object]:
    """The ADK reference set (#149): name -> its folder in the installed package.
    Found on disk, never imported — a reference is guide material, not a driver
    that `import shal` registers (D1)."""
    from importlib.resources import files
    root = files("shal") / "adk" / "reference"
    if not root.is_dir():
        return {}
    return {d.name: d for d in sorted(root.iterdir(), key=lambda d: d.name)
            if d.is_dir() and (d / "driver.py").is_file()}


def _reference_files(ref) -> list:
    """The files of one reference, in reading order: driver, sim twin, test,
    topology. A reference whose twin is its address (sqlite, ``":memory:"``) has
    no sim.py, so it is three."""
    order = {"driver.py": 0, "sim.py": 1, "topology.yaml": 3}
    picked = [f for f in ref.iterdir() if f.is_file()
              and (f.name in order or (f.name.startswith("test_") and f.name.endswith(".py")))]
    return sorted(picked, key=lambda f: (order.get(f.name, 2), f.name))


def _reference_summary(ref) -> str:
    """The first line of the driver's module docstring, read as text (no import)."""
    import ast
    doc = ast.get_docstring(ast.parse((ref / "driver.py").read_text(encoding="utf-8")))
    return (doc or "").strip().splitlines()[0] if doc else ""


def _reference_compatible(ref) -> str | None:
    """The driver class's ``compatible`` string, read as text (no import)."""
    import ast
    tree = ast.parse((ref / "driver.py").read_text(encoding="utf-8"))
    for cls in (n for n in tree.body if isinstance(n, ast.ClassDef)):
        for st in cls.body:
            if (isinstance(st, ast.Assign) and isinstance(st.value, ast.Constant)
                    and isinstance(st.value.value, str)
                    and any(isinstance(t, ast.Name) and t.id == "compatible"
                            for t in st.targets)):
                return st.value.value
    return None


def _docs_list_json() -> int:
    """`shal docs --list --json`: the ADK reference set (#185)."""
    refs = []
    for name, ref in _references().items():
        files = [f.name for f in _reference_files(ref)]
        drivers = " ".join(f"--drivers {f}" for f in files if f in ("driver.py", "sim.py"))
        refs.append({"name": name, "compatible": _reference_compatible(ref),
                     "summary": _reference_summary(ref), "folder": str(ref),
                     "files": files, "has_sim": "sim.py" in files,
                     "run_with": f"shal probe topology.yaml {drivers}",
                     "print_with": f"shal docs --example {name}"})
    _json_out({"ok": True, "references": refs})
    return 0


def _cmd_docs_list(as_json: bool = False) -> int:
    if as_json:
        return _docs_list_json()
    refs = _references()
    print("ADK reference drivers — guide material to copy, not registered drivers.")
    print("Each is driver.py, sim.py (its twin), test_<name>.py, topology.yaml — no sim.py")
    twins = [n for n, r in refs.items() if not (r / "sim.py").is_file()]
    print(f"when the address is the twin ({', '.join(twins) or 'none'}).")
    print()
    for name, ref in refs.items():
        print(f"  {name:<14} {_reference_summary(ref)}")
    print()
    print("Print one:  shal docs --example <name>")
    print("Run one:    shal probe topology.yaml --drivers driver.py --drivers sim.py")
    print("            (no sim.py: leave out --drivers sim.py)")
    return 0


def _cmd_docs_example(name: str) -> int:
    refs = _references()
    ref = refs.get(name)
    if ref is None:
        print(f"shal docs: no reference named '{name}' "
              f"(references: {', '.join(refs) or 'none'})", file=sys.stderr)
        return 2
    print(f"# ADK reference '{name}' — {_reference_summary(ref)}")
    print(f"# Folder: {ref}")
    files = _reference_files(ref)
    drivers = " ".join(f"--drivers {f.name}" for f in files if f.name in ("driver.py", "sim.py"))
    print(f"# Not registered by `import shal`. Copy the {len(files)} files, "
          f"or run them as they are:")
    print(f"#   shal probe topology.yaml {drivers}")
    for f in files:
        print()
        print(f"# ==== {f.name} " + "=" * max(4, 60 - len(f.name)))
        print(f.read_text(encoding="utf-8").rstrip())
    return 0


# Samples (#206): small programs for a PERSON deciding whether SHAL does their job.
# The ADK references above are for a cold agent writing a driver. Same mechanism,
# separate folders, separate flags: --list/--example never show a sample, and
# --samples/--sample never show a reference.
#: a sample folder's file for the `samples` CI job, not for the person: not printed,
#: not written by --to (see dev/samples/run_samples.py for its keys)
_SAMPLE_EXPECT = "expect.json"


def _samples() -> dict[str, object]:
    """The samples: name -> its folder in the installed package. Every subfolder of
    ``shal/samples`` holding a ``run.py`` is one; found on disk, never imported."""
    from importlib.resources import files
    root = files("shal") / "samples"
    if not root.is_dir():
        return {}
    return {d.name: d for d in sorted(root.iterdir(), key=lambda d: d.name)
            if d.is_dir() and (d / "run.py").is_file()}


def _sample_files(sample) -> list:
    """The files of one sample, ``run.py`` first, then the rest by name."""
    picked = [f for f in sample.iterdir() if f.is_file() and f.name != _SAMPLE_EXPECT
              and not f.name.endswith((".pyc", ".pyo"))]
    return sorted(picked, key=lambda f: (f.name != "run.py", f.name))


def _sample_summary(sample) -> str:
    """The first line of ``run.py``'s module docstring, read as text (no import)."""
    import ast
    doc = ast.get_docstring(ast.parse((sample / "run.py").read_text(encoding="utf-8")))
    return (doc or "").strip().splitlines()[0] if doc else ""


def _cmd_docs_samples(as_json: bool = False) -> int:
    samples = _samples()
    if as_json:
        _json_out({"ok": True, "samples": [
            {"name": name, "summary": _sample_summary(s), "folder": str(s),
             "files": [f.name for f in _sample_files(s)],
             "print_with": f"shal docs --sample {name}",
             "write_with": f"shal docs --sample {name} --to <DIR>"}
            for name, s in samples.items()]})
        return 0
    print("Samples — small programs that show what SHAL does. Each runs on a simulator.")
    print()
    for name, s in samples.items():
        print(f"  {name:<14} {_sample_summary(s)}")
    print()
    print("Print one:  shal docs --sample <name>")
    print("Write one:  shal docs --sample <name> --to <DIR>   (it prints the command that runs it)")
    return 0


def _cmd_docs_sample(name: str, to: str | None) -> int:
    samples = _samples()
    sample = samples.get(name)
    if sample is None:
        print(f"shal docs: no sample named '{name}' "
              f"(samples: {', '.join(samples) or 'none'})", file=sys.stderr)
        return 2
    files = _sample_files(sample)
    if to is not None:
        return _write_sample(name, files, to)
    print(f"# Sample '{name}' — {_sample_summary(sample)}")
    print(f"# Write it to a folder and run it:  shal docs --sample {name} --to <DIR>")
    for f in files:
        print()
        print(f"# ==== {f.name} " + "=" * max(4, 60 - len(f.name)))
        print(f.read_text(encoding="utf-8").rstrip())
    return 0


def _write_sample(name: str, files: list, to: str) -> int:
    """``--to DIR``: write the sample's files into DIR (made if missing) and print the
    one command that runs it on stdout — nothing else goes there. DIR must be empty:
    a sample never overwrites a file, and never mixes into someone's folder."""
    from pathlib import Path
    dest = Path(to)
    if dest.exists() and not dest.is_dir():
        print(f"shal docs: cannot write sample '{name}' to {to}: it is a file, "
              f"not a folder", file=sys.stderr)
        return 1
    if dest.is_dir() and any(dest.iterdir()):
        print(f"shal docs: cannot write sample '{name}' to {to}: the folder is not "
              f"empty, and a sample never overwrites or mixes with your files. "
              f"Give an empty or new folder.", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    for f in files:
        (dest / f.name).write_bytes(f.read_bytes())
    run_py = str(dest / "run.py")
    # pastes into bash, PowerShell and cmd when it can (see _shell_token); a path
    # with any other character is quoted as the best a single line can do
    token = _shell_token("./" + run_py if run_py.startswith("-") else run_py)
    print(f"shal docs: wrote sample '{name}' to {to}: "
          f"{', '.join(f.name for f in files)}", file=sys.stderr)
    print("python " + (token or f'"{run_py}"'))
    return 0


def _cmd_docs(args) -> int:
    """Print an in-package authoring doc so a pip-only agent has it offline: the
    provider-neutral 'add a device' guide by default, or the complete Driver & Bus SDK
    contract with --sdk. Both ship in the wheel as package data (#55, #97).
    ``--list`` names the ADK reference set and ``--example <name>`` prints one (#149).
    ``--samples`` names the samples and ``--sample <name>`` prints one, or writes it
    with ``--to DIR`` (#206)."""
    from importlib.resources import files
    if getattr(args, "json", False) and not (getattr(args, "list", False)
                                             or getattr(args, "samples", False)):
        msg = "--json works only with --list or --samples"
        print(f"shal docs: {msg}", file=sys.stderr)
        _json_out({"ok": False, "error": _error_obj(
            "UsageError", msg, "add --list or --samples")})
        return 2
    if getattr(args, "to", None) is not None and not getattr(args, "sample", None):
        print("shal docs: --to works only with --sample <name>", file=sys.stderr)
        return 2
    if getattr(args, "list", False):
        return _cmd_docs_list(getattr(args, "json", False))
    if getattr(args, "example", None):
        return _cmd_docs_example(args.example)
    if getattr(args, "samples", False):
        return _cmd_docs_samples(getattr(args, "json", False))
    if getattr(args, "sample", None):
        return _cmd_docs_sample(args.sample, getattr(args, "to", None))
    doc = "SDK.md" if getattr(args, "sdk", False) else "AGENT_GUIDE.md"
    print(_strip_front_matter((files("shal") / doc).read_text(encoding="utf-8")))
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # `check` errors go to stderr
        try:
            stream.reconfigure(encoding="utf-8")  # avoid Windows-codepage mojibake
        except Exception:
            pass
    # every subcommand can reach a driver op (probe/tools now, more later), so the
    # loop-policy choice is made once here rather than per subcommand (#94)
    _use_selector_loop_on_win32()

    ap = argparse.ArgumentParser(
        prog="shal",
        description="Drive a SHAL topology — read it, list its tools, or serve it.",
        epilog="Add a device: run `shal docs` (the bundled guide)  |  "
               "Full SDK: run `shal docs --sdk`")
    ap.add_argument("--version", action="version",
                    version=f"shal {importlib.metadata.version('pyshal')}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")

    p = sub.add_parser(
        "probe", help="one-shot read: print device state and exit (no MCP host)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run every read that needs no arguments (or one named read) and "
                    "print the values. Writes are named, never run.",
        epilog="--json prints one JSON document on stdout:\n"
               '  {"ok": true, "topology": "sim.yaml",\n'
               '   "reads": [{"tool": "ambient_temp__read_celsius", "device": "ambient_temp",\n'
               '              "op": "read_celsius", "ok": true, "value": 24.6,\n'
               '              "unit": "celsius"}],\n'
               '   "writes_not_run": [{"tool": "ambient_temp__set_target",\n'
               '              "device": "ambient_temp", "op": "set_target",\n'
               '              "side_effect": "config", "gated": true,\n'
               '              "run_with":\n'
               '                "shal call sim.yaml ambient_temp set_target celsius=<celsius>"}]}\n'
               '  A read that failed has "ok": false and "error" (an object: {"type",\n'
               '  "message", "fix"}) instead of "value"; the\n'
               '  top-level "ok" is still true (the probe ran, exit 0). With a named\n'
               '  tool, "reads" holds that one read. A gated write is refused by\n'
               '  `shal call` (exit 2) until a person approves it.\n'
               '  run_with: replace each name=<name> with a value. It pastes into bash,\n'
               '  PowerShell and cmd. A path of ASCII letters, digits and _ - . / : \\ +\n'
               '  is bare; one that also has a space or # = @ ~ is in double quotes.\n'
               '  Any other character (non-ASCII too), or a path ending in \\, makes\n'
               '  run_with null: build that call yourself. bash reads a \\ as an\n'
               '  escape, so use / in paths there. A path that starts with - is\n'
               '  written ./-x.yaml, so `shal call` does not read it as a flag.\n'
               "\n"
               "exit: 0 ran; 1 no such tool, the tool is a write, or the topology\n"
               "  does not load. The message is on stderr; with --json, stdout also\n"
               '  holds {"ok": false, "error": {"type": ..., "message": <the same\n'
               '  message>, "fix": <what fixes it>}}. 2 is a usage error.')
    p.add_argument("topology", help="path to the topology YAML")
    p.add_argument("tool", nargs="?", help="a specific read tool to run (default: all reads)")
    _add_drivers_arg(p)
    p.add_argument("--json", action="store_true",
                   help="print the reads and the writes not run as JSON on stdout")
    p.set_defaults(func=_cmd_probe)

    t = sub.add_parser(
        "tools", help="list the device tools (read / gated)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="List every tool on the topology with its kind: read, write, "
                    "or gated (needs a person's approval).",
        epilog="--json prints one JSON document on stdout:\n"
               '  {"ok": true, "topology": "sim.yaml",\n'
               '   "tools": [{"tool": "ambient_temp__set_target", "device": "ambient_temp",\n'
               '              "op": "set_target", "kind": "gated", "side_effect": "config",\n'
               '              "gated": true, "idempotent": false, "unit": "celsius",\n'
               '              "description": "<the full description>",\n'
               '              "input_schema": {<JSON Schema of the arguments>}}]}\n'
               "  Device ops only: the shal_approve / shal_deny tools that `shal mcp`\n"
               "  adds for a host are not in the list. The description is not cut.\n"
               '  A node with routes: each of its tools also has "routes": ["r1",\n'
               '  "r2"] (in order), and its input_schema an optional "via" (an enum\n'
               "  of those names) that pins one route: `shal call ... --via r2`.\n"
               "\n"
               "exit: 0 listed; 1 the topology does not load. The message is on\n"
               '  stderr; with --json, stdout also holds {"ok": false, "error":\n'
               '  {"type": ..., "message": ..., "fix": ...}}.')
    t.add_argument("topology", help="path to the topology YAML")
    _add_drivers_arg(t)
    t.add_argument("--json", action="store_true",
                   help="print every device op as JSON on stdout")
    t.set_defaults(func=_cmd_tools)

    m = sub.add_parser("mcp", help="serve the topology to an MCP host (the adapter)")
    m.add_argument("topology", nargs="?", default=os.environ.get("SHAL_TOPOLOGY"),
                   help="path to the topology YAML (or set SHAL_TOPOLOGY)")
    _add_drivers_arg(m)
    m.add_argument("--approve", choices=["gate", "auto"],
                   default=os.environ.get("SHAL_APPROVE", "gate"),
                   help="gate = reads free, writes need human approval (default); "
                        "auto = free writes (opt-out, recorded in the audit log)")
    m.set_defaults(func=_cmd_mcp)

    d = sub.add_parser(
        "docs", help="print the in-package 'add a device' agent guide",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="--list --json prints one JSON document on stdout:\n"
               '  {"ok": true,\n'
               '   "references": [{"name": "tmp102", "compatible": "ti,tmp102",\n'
               '                   "summary": "<first line of the driver docstring>",\n'
               '                   "folder": "<its folder in the installed package>",\n'
               '                   "files": ["driver.py", "sim.py", "test_tmp102.py",\n'
               '                             "topology.yaml"],\n'
               '                   "has_sim": true,\n'
               '                   "run_with": "shal probe topology.yaml --drivers driver.py '
               '--drivers sim.py",\n'
               '                   "print_with": "shal docs --example tmp102"}]}\n'
               "  A reference whose address is its twin (sqlite) has no sim.py.\n"
               "\n"
               "--samples --json prints one JSON document on stdout:\n"
               '  {"ok": true,\n'
               '   "samples": [{"name": "hello",\n'
               '                "summary": "<first line of the run.py docstring>",\n'
               '                "folder": "<its folder in the installed package>",\n'
               '                "files": ["run.py", "topology.yaml"],\n'
               '                "print_with": "shal docs --sample hello",\n'
               '                "write_with": "shal docs --sample hello --to <DIR>"}]}\n'
               "  --json works only with --list or --samples (exit 2 otherwise).\n"
               "\n"
               "--sample NAME --to DIR writes the sample's files into DIR and prints\n"
               "  the one command that runs it, alone on stdout (e.g. python DIR/run.py).\n"
               "  DIR may be new; an existing DIR must be empty, or nothing is written\n"
               "  (exit 1). An unknown NAME is exit 2.")
    dg = d.add_mutually_exclusive_group()
    dg.add_argument("--sdk", action="store_true",
                    help="print the full Driver & Bus SDK — the complete authoring contract")
    dg.add_argument("--list", action="store_true",
                    help="list the ADK reference drivers (examples to copy)")
    dg.add_argument("--example", metavar="NAME",
                    help="print one ADK reference: driver, sim twin, test, topology")
    dg.add_argument("--samples", action="store_true",
                    help="list the samples (small programs that show what SHAL does)")
    dg.add_argument("--sample", metavar="NAME",
                    help="print one sample, or write it to a folder with --to")
    d.add_argument("--to", metavar="DIR",
                   help="with --sample: write its files into DIR (new or empty) and "
                        "print the command that runs it")
    d.add_argument("--json", action="store_true",
                   help="with --list or --samples: print the list as JSON on stdout")
    d.set_defaults(func=_cmd_docs)

    c = sub.add_parser(
        "check", help="check a driver against the authoring contract (conformance)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run conformance.check_driver on one driver and print its report.",
        epilog="forms:\n"
               "  shal check vendor,part                 a registered compatible\n"
               "  shal check module:Class                a class by import path (the\n"
               "                                         cwd is on sys.path, so\n"
               "                                         driver:MyThing works)\n"
               "  shal check <target> --topology t.yaml  add the live probes on a sim\n"
               "\n"
               "exit: 0 no problems, 1 problems (warnings never fail), "
               "2 the check could not run. With --json a failure to run prints\n"
               '  {"ok": false, "error": {"type": ..., "message": ..., "fix": ...}}')
    c.add_argument("target", metavar="<compatible|module:Class>",
                   help="a registered compatible (shal,sim-sensor) or module:Class")
    c.add_argument("--topology", metavar="t.yaml", default=None,
                   help="a sim topology that binds this driver — runs the live probes")
    c.add_argument("--json", action="store_true",
                   help="print the report as JSON on stdout (ok, problems, warnings, checked)")
    c.set_defaults(func=_cmd_check)

    k = sub.add_parser(
        "call", help="run one op — reads and writes run; config/actuator ops are refused",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Run one device op and print its result. An op labelled none "
                    "or write runs. An op labelled config or actuator is refused "
                    "before it is called: nothing is sent to the device.",
        epilog="arguments:\n"
               "  Values map to the op's parameters in order, or name them:\n"
               "    shal call sim.yaml ambient_temp read_celsius --json\n"
               "    shal call lab.yaml psu set_voltage 3.3\n"
               "    shal call lab.yaml psu set_voltage volts=3.3\n"
               "  Each value is converted to the parameter's type (integer,\n"
               "  number, true/false, string). `shal tools <topology>` lists the\n"
               "  ops. Put `--` before a value that starts with '-' (a negative\n"
               "  number works without it).\n"
               "\n"
               "routes:\n"
               "  --via NAME pins one route of a node with routes for this call: no\n"
               "  failover, and a failure names that route. `shal routes <topology>\n"
               "  <device>` lists the names. An unknown name is exit 3, nothing sent;\n"
               '  with --json, stdout holds {"ok": false, "error": {"type", "message",\n'
               '  "fix"}, "routes":\n'
               '  [<the valid names>]}. A routed call\'s JSON has "via", the route\n'
               "  that carried it (on failure: the route that failed).\n"
               "\n"
               "approval:\n"
               "  There is NO --approve flag. The agent that runs a command cannot\n"
               "  approve its own call. A config or actuator op is approved in a host:\n"
               "    - an MCP host:  shal mcp <topology>  (a person approves each call)\n"
               "    - Python:       with shal.approver(<your Approver>): ...\n"
               "\n"
               "exit: 0 ran, 1 the op ran and failed (device error or limits),\n"
               "      2 refused by the gate (config/actuator; nothing sent),\n"
               "      3 could not run (usage error, unknown device, op or route, bad\n"
               "        value, topology does not load) — not 2, so a refusal is never\n"
               "        mistaken for a mistake\n"
               "\n"
               'errors: with --json every failure has "error": {"type": <short name>,\n'
               '  "message": <text>, "fix": <the command or change that fixes it>}.')
    k.error = _call_usage_error(k, argv)  # 2 is the gate's code, not argparse's
    k.add_argument("topology", help="path to the topology YAML")
    k.add_argument("node", help="the device: its id, its /path, or its handle in `shal tools`")
    k.add_argument("op", help="the op to run (e.g. read_celsius)")
    k.add_argument("args", nargs="*", metavar="value",
                   help="op arguments: values in parameter order, or name=value")
    k.add_argument("--json", action="store_true",
                   help="print the result (or the refusal) as JSON on stdout")
    k.add_argument("--via", metavar="NAME", default=None,
                   help="pin one route of a node with routes (see `shal routes`)")
    _add_drivers_arg(k)
    k.set_defaults(func=_cmd_call)

    rc = sub.add_parser(
        "records", help="read the record store (read-only, no topology)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read `record.write()`'s store under DIR (`DIR/records.db`, and "
                    "the `records/` YAML audit copy beside it — `record.md` §4 "
                    "'the YAML wins'), filtered, newest first. side_effect: none.",
        epilog="--json prints one JSON document on stdout:\n"
               '  {"ok": true, "store": "DIR/records.db",\n'
               '   "records": [<record, as record.to_json() decodes it>]}\n'
               "  --skip-newer also adds:\n"
               '   "skipped": [{"id": "<record id>", "record_version": <int>}]\n'
               "\n"
               "no store (no records.db in DIR): exit 1. The message is on stderr;\n"
               '  --json also prints {"ok": false,\n'
               '    "error": {"type": "NoStore", "message": "no records.db in DIR",\n'
               '              "fix": "<the one command that makes one>"}}\n'
               "\n"
               "a record written by a newer record_version refuses the whole read\n"
               "  (one sentence, exit 1) unless --skip-newer, which returns the\n"
               "  readable records and lists the ones it skipped instead. --json\n"
               '  prints {"ok": false, "error": {"type": "NewerRecord",\n'
               '    "message": "<the one sentence>",\n'
               '    "fix": "re-run with --skip-newer, or upgrade pyshal"}}\n'
               "\n"
               "a malformed record (not newer, e.g. a required key missing) is a\n"
               '  different failure: --json prints {"ok": false, "error":\n'
               '    {"type": "BadRecord", "message": "<the one sentence>",\n'
               '     "fix": "fix or remove the record file named in the message"}}\n'
               "  — every --json error from this command is {type, message, fix}.\n"
               "\n"
               "exit: 0 read (even zero records); 1 no store, or a newer record\n"
               "  refused without --skip-newer; 2 a usage error.")
    rc.add_argument("dir", nargs="?", default=".", metavar="DIR",
                    help="the directory holding records.db (default: .)")
    rc.add_argument("--unit", default=None, help="filter: exact unit id")
    rc.add_argument("--station", default=None, help="filter: exact station id")
    rc.add_argument("--sequence", default=None, help="filter: exact sequence name")
    rc.add_argument("--verdict", default=None,
                    choices=["pass", "fail", "error", "aborted"],
                    help="filter: exact verdict")
    rc.add_argument("--last", type=int, default=None, metavar="N",
                    help="keep only the N most recent (after the filters above)")
    rc.add_argument("--skip-newer", action="store_true", dest="skip_newer",
                    help="skip records from a newer record_version instead of "
                         "refusing the whole read (record.read(newer='skip'))")
    rc.add_argument("--json", action="store_true",
                    help="print the records as JSON on stdout")
    rc.set_defaults(func=_cmd_records)

    r = sub.add_parser(
        "routes", help="list the routes a node declares, in order",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Print the routes the topology declares for one device, in "
                    "order: each route's name, the bus it goes via and its address. "
                    "A device without routes shows its one main route. This is the "
                    "declaration only, not whether a route is up. A URL or "
                    "user@host address is shown without its credentials.",
        epilog="--json prints one JSON document on stdout:\n"
               '  {"ok": true, "topology": "t.yaml", "device": "board",\n'
               '   "path": "/console/board",\n'
               '   "routes": [{"name": "console", "via": "/console", "address": 72},\n'
               '              {"name": "ssh", "via": "/ssh", "address": "10.0.0.5"}]}\n'
               "  The first route is the main one. Pin a route for one call:\n"
               "    shal call t.yaml board <op> --via ssh\n"
               "\n"
               "exit: 0 listed; 1 no such device, or the topology does not load. The\n"
               "  message is on stderr; with --json, stdout also holds\n"
               '  {"ok": false, "error": {"type": ..., "message": ..., "fix": ...}}.')
    r.add_argument("topology", help="path to the topology YAML")
    r.add_argument("node", help="the device: its id, its /path, or its handle in `shal tools`")
    _add_drivers_arg(r)
    r.add_argument("--json", action="store_true",
                   help="print the routes as JSON on stdout")
    r.set_defaults(func=_cmd_routes)

    for cmd, sp in (("probe", p), ("tools", t), ("docs", d), ("check", c),
                    ("records", rc), ("routes", r)):
        sp.error = _usage_error(sp, cmd, 2, argv)

    args, extra = ap.parse_known_args(argv)
    if extra:  # parse_args would exit 2 here — for `call`, 2 is the gate's code
        hooked = {"call": k, "probe": p, "tools": t, "docs": d, "check": c,
                  "records": rc, "routes": r}
        if args.cmd in hooked:
            hooked[args.cmd].error(f"unrecognized arguments: {' '.join(extra)}")
        ap.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
