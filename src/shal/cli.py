"""``shal`` — the base command-line front door (issue #54).

SHAL stands on its own *without* MCP: this CLI is the primary way a human (or a
shell agent) drives a topology. MCP is one subcommand here (``shal mcp``), an
adapter — not the front door.

    shal probe lab.yaml                  # one-shot: print device state and exit
    shal probe lab.yaml dev__get_state   # read one named tool
    shal tools lab.yaml                  # list the device tools (read / gated)
    shal call lab.yaml dev read_celsius --json   # run one op; a gated op is refused (exit 2)
    shal mcp   lab.yaml                  # serve to an MCP host (the adapter)
    shal probe lab.yaml --drivers ./drivers/   # load local/unpackaged drivers
    shal check shal,sim-sensor --json    # driver conformance as a JSON report
    shal check driver:MyThing --topology sim.yaml   # a local class + live sim probes

The legacy ``shal-mcp`` command still works (it is ``shal mcp``).

These commands are thin views over the same core (``shal.load`` → ``Bridge``);
the read/serve/driver-loading logic is shared with ``shal.mcp.server`` so there
is one implementation, not two.
"""
from __future__ import annotations

import argparse
import asyncio
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


# `--json` on probe / tools / docs --list (shal#185). One JSON document on stdout,
# the same exit code as without --json, and every message still on stderr (as
# `shal call --json` does). An error is `{"ok": false, "error": <the message>}`.
def _json_out(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))


def _json_error(msg: str, code: int = 1) -> int:
    print(msg, file=sys.stderr)
    _json_out({"ok": False, "error": msg})
    return code


def _json_load(args, cmd: str):
    """Load the topology for a `--json` command: the Hal, or the exit code after the
    error was reported. The shared loaders exit with a message (exit 1); any other
    load failure is exit 1 too, as its traceback is without --json."""
    from .mcp.server import _import_drivers, _resolve_hal
    try:
        _import_drivers(args.drivers)
        return _resolve_hal(args.topology)
    except SystemExit as e:
        if not isinstance(e.code, str):
            raise
        return _json_error(e.code)
    except Exception as e:  # noqa: BLE001 - a bad topology is a JSON error, exit 1
        return _json_error(f"shal {cmd}: cannot load {args.topology}: "
                           f"{type(e).__name__}: {e}")


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
    tokens = [args.topology, fact["device"], fact["op"]]
    for d in args.drivers:
        tokens += ["--drivers", d]
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
            return _json_error(str(e.code))
        reads, writes = _probe_split(defs)
        read_out = []
        for d in picked or reads:
            f = facts[d["name"]]
            entry = {"tool": d["name"], "device": f["device"], "op": f["op"]}
            try:
                out = bridge.call(d["name"], {})
            except Exception as e:  # noqa: BLE001 - as the text snapshot: one bad read
                if picked:  # a named read that raises exits 1 without --json too
                    return _json_error(f"shal probe: {d['name']} failed: "
                                       f"{type(e).__name__}: {e}")
                entry.update(ok=False, error=f"{type(e).__name__}: {e}")
            else:
                if out.get("ok"):
                    entry.update(ok=True, value=out.get("result"))
                else:
                    entry.update(ok=False, error=out.get("error", out.get("message")))
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
                          "input_schema": d["input_schema"]})
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


def _check_fail(msg: str) -> int:
    print(f"shal check: {msg}", file=sys.stderr)
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
        return _check_fail(f"topology file not found: {args.topology}")
    try:
        cls = _load_check_target(args.target)
    except ValueError as e:
        return _check_fail(str(e))
    try:
        report = check_driver(cls, args.topology)
    except Exception as e:  # noqa: BLE001 - the check could not run: no traceback
        return _check_fail(f"check could not run: {type(e).__name__}: {e}")
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
    """A `shal call` mistake: printed on stderr, exit 3, never a traceback."""


def _call_usage_error(parser: argparse.ArgumentParser):
    """argparse exits 2 on a usage error; for `call`, 2 means a gate refusal."""
    def error(message: str):
        parser.print_usage(sys.stderr)
        print(f"shal call: {message}", file=sys.stderr)
        raise SystemExit(_CALL_CANNOT_RUN)
    return error


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
    ``inferred_side_effect`` and ``get_gated_effects()`` (one source of truth, D4),
    and the op that does run runs under ``DenyAll``, so the op-layer gate still
    backs this up: anything that reached it would be denied, never prompted or
    passed. It reads the loaded Hal's gated set (issue #114, ADR-001 addendum
    5b: the topology's ``policy:`` ∪ the host's widenings), not the shipped
    default, so this refusal and the runtime gate cannot disagree. There is no
    flag: the caller cannot choose its own gate."""
    from .approval import DenyAll, approver
    from .driver import inferred_side_effect
    from .errors import HOW_TO_APPROVE_LINE
    from .mcp.server import _import_drivers, _resolve_hal

    def emit(payload: dict) -> None:
        if args.json:
            print(json.dumps(payload, indent=2, default=str))

    if not os.path.isfile(args.topology):
        print(f"shal call: topology file not found: {args.topology}", file=sys.stderr)
        return _CALL_CANNOT_RUN
    try:
        _import_drivers(args.drivers)
        hal = _resolve_hal(args.topology)
    except SystemExit as e:  # the shared loaders exit with a message, not a code
        print(e.code if isinstance(e.code, str) else f"shal call: load failed ({e.code})",
              file=sys.stderr)
        return _CALL_CANNOT_RUN
    except Exception as e:  # noqa: BLE001 - a bad topology is a clean exit 3
        print(f"shal call: cannot load {args.topology}: {type(e).__name__}: {e}",
              file=sys.stderr)
        return _CALL_CANNOT_RUN
    try:
        name, node, fn = _find_call_tool(hal, args.node, args.op)
        device = node.id or node.path
        side_effect = inferred_side_effect(fn)
        if side_effect in hal.get_gated_effects():  # from the label: never invoked
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
                  "error": msg})
            return _CALL_REFUSED
        schema = next(s["input_schema"] for s in hal.tool_schemas() if s["name"] == name)
        arguments = _call_arguments(fn, schema, args.args)
        with approver(DenyAll()):
            out = hal.call_tool(name, arguments)
    except _CallCannotRun as e:
        print(f"shal call: {e}", file=sys.stderr)
        return _CALL_CANNOT_RUN
    except Exception as e:  # noqa: BLE001 - the op raised past call_tool: no traceback
        print(f"shal call: {args.node}.{args.op} failed: {type(e).__name__}: {e}",
              file=sys.stderr)
        return _CALL_FAILED
    finally:
        hal.close()
    payload = {"ok": out["ok"], "tool": name, "device": device, "op": args.op,
               "side_effect": side_effect, **{k: v for k, v in out.items() if k != "ok"}}
    if not out["ok"]:
        print(f"shal call: {device}.{args.op} failed: {out.get('error')}", file=sys.stderr)
        emit(payload)
        return _CALL_FAILED
    if args.json:
        emit(payload)
    else:
        print("ok" if out["result"] is None else json.dumps(out["result"], default=str))
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


def _cmd_docs(args) -> int:
    """Print an in-package authoring doc so a pip-only agent has it offline: the
    provider-neutral 'add a device' guide by default, or the complete Driver & Bus SDK
    contract with --sdk. Both ship in the wheel as package data (#55, #97).
    ``--list`` names the ADK reference set and ``--example <name>`` prints one (#149)."""
    from importlib.resources import files
    if getattr(args, "json", False) and not getattr(args, "list", False):
        print("shal docs: --json works only with --list", file=sys.stderr)
        return 2
    if getattr(args, "list", False):
        return _cmd_docs_list(getattr(args, "json", False))
    if getattr(args, "example", None):
        return _cmd_docs_example(args.example)
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
               '  A read that failed has "ok": false and "error" instead of "value"; the\n'
               '  top-level "ok" is still true (the probe ran, exit 0). With a named\n'
               '  tool, "reads" holds that one read. A gated write is refused by\n'
               '  `shal call` (exit 2) until a person approves it.\n'
               '  run_with: replace each name=<name> with a value. It pastes into bash,\n'
               '  PowerShell and cmd. A path of ASCII letters, digits and _ - . / : \\ +\n'
               '  is bare; one that also has a space or # = @ ~ is in double quotes.\n'
               '  Any other character (non-ASCII too), or a path ending in \\, makes\n'
               '  run_with null: build that call yourself. bash reads a \\ as an\n'
               '  escape, so use / in paths there.\n'
               "\n"
               "exit: 0 ran; 1 no such tool, the tool is a write, or the topology\n"
               "  does not load. The message is on stderr; with --json, stdout also\n"
               '  holds {"ok": false, "error": <the same message>}. 2 is a usage error.')
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
               "\n"
               "exit: 0 listed; 1 the topology does not load. The message is on\n"
               '  stderr; with --json, stdout also holds {"ok": false, "error": ...}.')
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
               "  --json works only with --list (exit 2 otherwise).")
    dg = d.add_mutually_exclusive_group()
    dg.add_argument("--sdk", action="store_true",
                    help="print the full Driver & Bus SDK — the complete authoring contract")
    dg.add_argument("--list", action="store_true",
                    help="list the ADK reference drivers (examples to copy)")
    dg.add_argument("--example", metavar="NAME",
                    help="print one ADK reference: driver, sim twin, test, topology")
    d.add_argument("--json", action="store_true",
                   help="with --list: print the references as JSON on stdout")
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
               "2 the check could not run")
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
               "approval:\n"
               "  There is NO --approve flag. The agent that runs a command cannot\n"
               "  approve its own call. A config or actuator op is approved in a host:\n"
               "    - an MCP host:  shal mcp <topology>  (a person approves each call)\n"
               "    - Python:       with shal.approver(<your Approver>): ...\n"
               "\n"
               "exit: 0 ran, 1 the op ran and failed (device error or limits),\n"
               "      2 refused by the gate (config/actuator; nothing sent),\n"
               "      3 could not run (usage error, unknown device or op, bad value,\n"
               "        topology does not load) — not 2, so a refusal is never\n"
               "        mistaken for a mistake")
    k.error = _call_usage_error(k)  # 2 is the gate's code, not argparse's
    k.add_argument("topology", help="path to the topology YAML")
    k.add_argument("node", help="the device: its id, its /path, or its handle in `shal tools`")
    k.add_argument("op", help="the op to run (e.g. read_celsius)")
    k.add_argument("args", nargs="*", metavar="value",
                   help="op arguments: values in parameter order, or name=value")
    k.add_argument("--json", action="store_true",
                   help="print the result (or the refusal) as JSON on stdout")
    _add_drivers_arg(k)
    k.set_defaults(func=_cmd_call)

    args, extra = ap.parse_known_args(argv)
    if extra:  # parse_args would exit 2 here — for `call`, 2 is the gate's code
        if args.cmd == "call":
            k.error(f"unrecognized arguments: {' '.join(extra)}")
        ap.error(f"unrecognized arguments: {' '.join(extra)}")
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
