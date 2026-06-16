"""``shal`` — the base command-line front door (issue #54).

SHAL stands on its own *without* MCP: this CLI is the primary way a human (or a
shell agent) drives a topology. MCP is one subcommand here (``shal mcp``), an
adapter — not the front door.

    shal probe lab.yaml                  # one-shot: print device state and exit
    shal probe lab.yaml dev__get_state   # read one named tool
    shal tools lab.yaml                  # list the device tools (read / gated)
    shal mcp   lab.yaml                  # serve to an MCP host (the adapter)
    shal probe lab.yaml --drivers ./drivers/   # load local/unpackaged drivers

The legacy ``shal-mcp`` command still works (it is ``shal mcp``).

These commands are thin views over the same core (``shal.load`` → ``Bridge``);
the read/serve/driver-loading logic is shared with ``shal.mcp.server`` so there
is one implementation, not two.
"""
from __future__ import annotations

import argparse
import os


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


def _cmd_docs(args) -> int:
    """Print the provider-neutral in-package agent guide (how to add a device).
    Ships in the wheel as package data, so a pip-only agent has it offline (#55)."""
    from importlib.resources import files
    print((files("shal") / "AGENT_GUIDE.md").read_text(encoding="utf-8"))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")  # avoid Windows-codepage mojibake
    except Exception:
        pass

    ap = argparse.ArgumentParser(
        prog="shal",
        description="Drive a SHAL topology — read it, list its tools, or serve it.",
        epilog="Add a device: run `shal docs` (the bundled guide)  |  "
               "Full SDK: https://github.com/determlab/shal/blob/main/docs/SDK.md")
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
    d.set_defaults(func=_cmd_docs)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
