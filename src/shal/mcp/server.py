"""``shal-mcp`` — serve a SHAL topology to any MCP host as gated tools (#25).

Thin stdio shell over `Bridge`. The `mcp` SDK is an optional extra
(``pip install pyshal[mcp]``) and is imported only when the command runs, so the
core package stays at two dependencies.

    shal-mcp lab.yaml                 # reads free, writes gated (default)
    shal-mcp lab.yaml --approve auto  # opt in to free writes (logged)
    SHAL_TOPOLOGY=lab.yaml shal-mcp   # topology via env

Register it with a host (example — Claude Code / Desktop ``mcpServers`` block):

    {"mcpServers": {"shal": {"command": "shal-mcp", "args": ["lab.yaml"]}}}
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os

from .bridge import Bridge


def _build_server(bridge: Bridge):
    """Wire a Bridge to a low-level MCP Server (imports the `mcp` SDK)."""
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server

    server: Server = Server("shal")

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return [
            types.Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["input_schema"],
                annotations=types.ToolAnnotations(**(d.get("annotations") or {})),
            )
            for d in bridge.tool_defs()
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None):
        result = bridge.call(name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server, stdio_server


def _import_drivers(paths: list[str]) -> None:
    """Import local/unpackaged driver module(s) so their ``@shal.register`` runs
    before the topology is loaded (issue #47). Each path is a ``.py`` file or a
    directory of them (``_``-prefixed files skipped); the containing directory is
    put on ``sys.path`` first so sibling imports (e.g. a driver importing its bus)
    resolve. Operator-controlled on the command line — the topology YAML stays
    pure data and never imports code."""
    import importlib
    import sys
    from pathlib import Path

    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            raise SystemExit(f"shal-mcp: --drivers path not found: {p}")
        files = sorted(p.glob("*.py")) if p.is_dir() else [p]
        root = p if p.is_dir() else p.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for f in files:
            if f.suffix != ".py" or f.name.startswith("_"):
                continue
            try:
                importlib.import_module(f.stem)
            except Exception as e:
                raise SystemExit(f"shal-mcp: failed importing driver '{f}': "
                                 f"{type(e).__name__}: {e}") from e


def _resolve_hal(topology: str | None):
    """Load the Hal from a topology YAML (CLI argument or ``SHAL_TOPOLOGY``).
    Raises SystemExit with a friendly message if none was given, or if a
    compatible can't be resolved (with a pointer to ``--drivers``)."""
    import shal

    if not topology:
        raise SystemExit(
            "shal-mcp: provide a topology YAML (as an argument or via SHAL_TOPOLOGY). "
            "See examples/demos/ for ready-to-edit topologies.")
    try:
        return shal.load(topology)
    except shal.LoadError as e:
        if "no driver installed" in str(e):
            raise SystemExit(
                f"{e}\n  If that driver is a local/unpackaged module, load it with "
                f"--drivers <file.py | directory/> (repeatable).") from e
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="shal-mcp",
        description="Serve a SHAL topology to an MCP host as gated tools.")
    ap.add_argument("topology", nargs="?", default=os.environ.get("SHAL_TOPOLOGY"),
                    help="path to the topology YAML (or set SHAL_TOPOLOGY)")
    ap.add_argument("--drivers", action="append", default=[], metavar="PATH",
                    help="import local driver module(s) before loading — a .py file "
                         "or a directory of them (repeatable). For topologies that use "
                         "unpackaged drivers (registered via @shal.register).")
    ap.add_argument("--approve", choices=["gate", "auto"],
                    default=os.environ.get("SHAL_APPROVE", "gate"),
                    help="gate = reads free, writes need human approval (default); "
                         "auto = free writes (opt-out, recorded in the audit log)")
    args = ap.parse_args(argv)

    _import_drivers(args.drivers)
    hal = _resolve_hal(args.topology)
    bridge = Bridge(hal, free_writes=(args.approve == "auto"))
    server, stdio_server = _build_server(bridge)

    async def _run() -> None:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    try:
        asyncio.run(_run())
    finally:
        hal.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
