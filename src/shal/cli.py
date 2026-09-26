"""``shal`` — the base command-line front door (issue #54).

SHAL stands on its own *without* MCP: this CLI is the primary way a human (or a
shell agent) drives a topology. MCP is one subcommand here (``shal mcp``), an
adapter — not the front door.

    shal probe lab.yaml                  # one-shot: print device state and exit
    shal probe lab.yaml dev__get_state   # read one named tool
    shal tools lab.yaml                  # list the device tools (read / gated)
    shal mcp   lab.yaml                  # serve to an MCP host (the adapter)
    shal probe lab.yaml --drivers ./drivers/   # load local/unpackaged drivers
    shal check ti,tmp102 --json          # driver conformance as a JSON report
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


def _cmd_probe(args) -> int:
    from .mcp import Bridge
    from .mcp.server import _import_drivers, _probe, _resolve_hal
    _import_drivers(args.drivers)
    hal = _resolve_hal(args.topology)
    try:
        return _probe(Bridge(hal), args.tool or None)
    finally:
        hal.close()


def _cmd_tools(args) -> int:
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
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001 - any import failure is a clean exit 2
        raise ValueError(f"failed importing module '{mod_name}': "
                         f"{type(e).__name__}: {e}") from e
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


def _cmd_docs(args) -> int:
    """Print an in-package authoring doc so a pip-only agent has it offline: the
    provider-neutral 'add a device' guide by default, or the complete Driver & Bus SDK
    contract with --sdk. Both ship in the wheel as package data (#55, #97)."""
    from importlib.resources import files
    doc = "SDK.md" if getattr(args, "sdk", False) else "AGENT_GUIDE.md"
    print(_strip_front_matter((files("shal") / doc).read_text(encoding="utf-8")))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # avoid Windows-codepage mojibake
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

    p = sub.add_parser("probe", help="one-shot read: print device state and exit (no MCP host)")
    p.add_argument("topology", help="path to the topology YAML")
    p.add_argument("tool", nargs="?", help="a specific read tool to run (default: all reads)")
    _add_drivers_arg(p)
    p.set_defaults(func=_cmd_probe)

    t = sub.add_parser("tools", help="list the device tools (read / gated)")
    t.add_argument("topology", help="path to the topology YAML")
    _add_drivers_arg(t)
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

    d = sub.add_parser("docs", help="print the in-package 'add a device' agent guide")
    d.add_argument("--sdk", action="store_true",
                   help="print the full Driver & Bus SDK — the complete authoring contract")
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
                   help="a registered compatible (ti,tmp102) or module:Class")
    c.add_argument("--topology", metavar="t.yaml", default=None,
                   help="a sim topology that binds this driver — runs the live probes")
    c.add_argument("--json", action="store_true",
                   help="print the report as JSON on stdout (ok, problems, warnings, checked)")
    c.set_defaults(func=_cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
