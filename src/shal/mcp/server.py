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


def _resolve_hal(topology: str | None, device: str | None, address: str | None):
    """Turn the CLI inputs into a loaded Hal: an explicit topology file, or the
    curated zero-config path (`--device sonos` → LAN discovery, with `--address`
    / `sim` to skip the scan). Raises SystemExit with a friendly message."""
    import shal

    if device:
        from .. import discovery
        if address:
            addresses = [address]
        else:
            addresses = discovery.discover(device)
            if not addresses:
                raise SystemExit(
                    f"shal-mcp: no '{device}' found on the LAN — pass --address <ip>, "
                    f"use --address sim for the simulator, or supply a topology YAML.")
        return shal.load(discovery.build_topology(device, addresses))
    if topology:
        return shal.load(topology)
    raise SystemExit(
        "shal-mcp: provide a topology YAML (arg or SHAL_TOPOLOGY), or --device <name> "
        "for curated zero-config discovery.")


def main(argv: list[str] | None = None) -> int:
    from .. import discovery

    ap = argparse.ArgumentParser(
        prog="shal-mcp",
        description="Serve a SHAL topology to an MCP host as gated tools.",
        epilog=f"curated --device values: {', '.join(discovery.supported())}")
    ap.add_argument("topology", nargs="?", default=os.environ.get("SHAL_TOPOLOGY"),
                    help="path to the topology YAML (or set SHAL_TOPOLOGY)")
    ap.add_argument("--device", default=None,
                    help="curated zero-config: discover a bundled hero device on the "
                         "LAN (e.g. 'sonos') instead of supplying a topology")
    ap.add_argument("--address", default=None,
                    help="with --device: the device IP/host (skips discovery), or "
                         "'sim' for the built-in simulator")
    ap.add_argument("--approve", choices=["gate", "auto"],
                    default=os.environ.get("SHAL_APPROVE", "gate"),
                    help="gate = reads free, writes need human approval (default); "
                         "auto = free writes (opt-out, recorded in the audit log)")
    args = ap.parse_args(argv)

    hal = _resolve_hal(args.topology, args.device, args.address)
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
