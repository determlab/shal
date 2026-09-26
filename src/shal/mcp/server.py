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
import sys

from ..cli import _use_selector_loop_on_win32
from .bridge import Bridge


def _build_server(bridge: Bridge):
    """Wire a Bridge to a low-level MCP Server (imports the `mcp` SDK)."""
    try:
        import mcp.types as types
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
    except ImportError as e:  # cold user without the optional extra (#71)
        raise SystemExit(
            'shal-mcp: serving an MCP host needs the MCP extra — '
            'pip install "pyshal[mcp]".\n'
            "  (Reads need no extra: try  shal probe <topology>.)") from e

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

    import anyio

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict | None):
        # Run the synchronous op OFF the server's event-loop thread (#92). A driver that
        # wraps an async library keeps its own loop and does run_until_complete(); calling
        # it inline here would nest a loop in the running server thread and raise
        # "Cannot run the event loop while another loop is running". A worker thread also
        # stops a slow/blocking op from freezing the whole server.
        result = await anyio.to_thread.run_sync(bridge.call, name, arguments)
        return [types.TextContent(type="text", text=json.dumps(result))]

    return server, stdio_server


def _import_drivers(paths: list[str]) -> None:
    """Import local/unpackaged driver module(s) so their ``@shal.register`` runs
    before the topology is loaded (issue #47). Each path is a ``.py`` file or a
    directory of them; the containing directory is put on ``sys.path`` first so
    sibling imports (e.g. a driver importing its bus) resolve. A directory scan
    skips ``_``-prefixed files (``__init__`` / private helpers), but a file named
    **explicitly** on the command line is always imported even if it starts with
    ``_`` (#85 — no silent skip). Operator-controlled on the command line — the
    topology YAML stays pure data and never imports code.

    The approval policy (gated set + approver) is the operator's, never a
    driver's (ADR-001 addendum 5): it is snapshotted before each import, and a
    module that changed it is refused — the policy restored, the attempt audited
    (``outcome="policy-changed"``) and ``LoadError("<module> changed the approval
    policy at import")`` raised."""
    import importlib
    import logging
    import sys
    from pathlib import Path

    from ..driver import _policy_changed, _policy_snapshot, _restore_policy
    from ..errors import LoadError

    for raw in paths:
        p = Path(raw).resolve()
        if not p.exists():
            raise SystemExit(f"shal-mcp: --drivers path not found: {p}")
        if p.is_dir():
            files = [f for f in sorted(p.glob("*.py")) if not f.name.startswith("_")]
            root = p
        else:
            files = [p]                      # explicitly named -> load even if _-prefixed
            root = p.parent
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        for f in files:
            if f.suffix != ".py":
                continue
            before = _policy_snapshot()
            try:
                importlib.import_module(f.stem)
            except Exception as e:
                raise SystemExit(f"shal-mcp: failed importing driver '{f}': "
                                 f"{type(e).__name__}: {e}") from e
            changed = _policy_changed(before)
            if changed:
                _restore_policy(before)
                logging.getLogger("shal.audit").info(
                    "%s changed the approval policy at import (%s); refused",
                    f.stem, ", ".join(changed),
                    extra={"event": "audit", "outcome": "policy-changed",
                           "file": str(f), "changed": changed})
                raise LoadError(f"{f.stem} changed the approval policy at import")


def _resolve_hal(topology: str | None):
    """Load the Hal from a topology YAML (CLI argument or ``SHAL_TOPOLOGY``).
    Raises SystemExit with a friendly message if none was given, or if a
    compatible can't be resolved (with a pointer to ``--drivers``)."""
    import shal

    if not topology:
        raise SystemExit(
            "shal-mcp: provide a topology YAML (as an argument or via SHAL_TOPOLOGY). "
            "See examples/demos/ for ready-to-edit topologies.")
    from pathlib import Path
    if not Path(topology).exists():
        # given-but-missing path: friendly, like the None and --drivers cases (#71)
        raise SystemExit(
            f"shal-mcp: topology file not found: {topology}\n"
            "  Pass the path to a topology YAML — see examples/demos/ for ready-to-edit ones.")
    try:
        return shal.load(topology)
    except shal.LoadError as e:
        if "no driver installed" in str(e):
            # Signpost the two ways forward — never a dead end (#42).
            raise SystemExit(
                f"{e}\n"
                f"  - Already have the driver as a local file? Load it:\n"
                f"      --drivers <file.py | directory/>   (repeatable)\n"
                f"  - Device not supported yet? Most devices have a Python library —\n"
                f"      wrap it as a driver in a few lines: run  shal docs  for the\n"
                f"      guide, then load your driver with --drivers.") from e
        raise


def _is_read(d: dict) -> bool:
    return bool((d.get("annotations") or {}).get("readOnlyHint"))


def _probe_pick(defs: list[dict], which: str) -> dict:
    """The tool def ``shal probe <topology> <which>`` runs. SystemExit with the
    message if there is no such tool, or if it is not a read. Shared with
    ``shal probe --json`` (#185) so both give the same refusal."""
    d = next((x for x in defs if x["name"] == which), None)
    if d is None:
        # `shal probe` users see these too (#166): a neutral `shal:` prefix, and
        # only commands both entry points have (they ship in one package).
        raise SystemExit(f"shal: no tool '{which}'. Run `shal probe <topology>` "
                         f"(no tool) to list what this topology exposes.")
    if not _is_read(d):
        raise SystemExit(f"shal: `shal probe` runs reads only; '{which}' changes "
                         f"hardware — use `shal call` (gated ops are refused "
                         f"until approved).")
    return d


def _probe_split(defs: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the reads a snapshot runs, the writes it names and does not run). A read
    that needs arguments is in neither; the MCP-only approve/deny tools are not
    writes. Shared with ``shal probe --json`` (#185): one list, one footer."""
    reads = [d for d in defs if _is_read(d) and not d["input_schema"].get("required")]
    writes = [d for d in defs if not _is_read(d)
              and d["name"] not in ("shal_approve", "shal_deny")]
    return reads, writes


def _probe(bridge, which: str | None) -> int:
    """One-shot, human-runnable read (issue #39): print real device state to the
    terminal and exit — no MCP host needed. Reads only (writes are gated; run the
    server for those). Uses only the pure Bridge, so it works without the `mcp`
    extra installed.

    ``--probe`` with no tool snapshots every no-arg read; ``--probe <tool>`` runs
    one named read."""
    defs = bridge.tool_defs()
    if which:
        _probe_pick(defs, which)
        out = bridge.call(which, {})
        print(json.dumps(out.get("result", out) if isinstance(out, dict) else out))
        return 0

    reads, writes = _probe_split(defs)
    print(f"# {len(reads)} read(s), {len(writes)} write(s) on this topology")
    for d in reads:
        try:
            out = bridge.call(d["name"], {})
            print(f"{d['name']}: {out.get('result', out) if isinstance(out, dict) else out}")
        except Exception as e:  # one bad device shouldn't sink the whole snapshot
            print(f"{d['name']}: <error: {type(e).__name__}: {e}>")
    if writes:
        # One line for `shal probe` and the legacy `shal-mcp` probe flag alike (#166):
        # `shal probe` is the front door, and `shal call` ships in the same package.
        print("# writes — not run by `shal probe`; use `shal call` "
              "(gated ops are refused until approved): "
              + ", ".join(d["name"] for d in writes))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="shal-mcp",
        description="Serve a SHAL topology to an MCP host as gated tools.",
        epilog="Docs: https://github.com/determlab/shal#readme  |  "
               "Write a driver: run `shal docs --sdk` (the full authoring contract)")
    ap.add_argument("topology", nargs="?", default=os.environ.get("SHAL_TOPOLOGY"),
                    help="path to the topology YAML (or set SHAL_TOPOLOGY)")
    ap.add_argument("--drivers", action="append", default=[], metavar="PATH",
                    help="import local driver module(s) before loading — a .py file "
                         "or a directory of them (repeatable). For topologies that use "
                         "unpackaged drivers (registered via @shal.register).")
    ap.add_argument("--probe", nargs="?", const="", default=None, metavar="TOOL",
                    help="don't start the server — print a real device reading and "
                         "exit (reads only). No value: snapshot every read. With a "
                         "tool name: run that one read. Needs no MCP host.")
    ap.add_argument("--approve", choices=["gate", "auto"],
                    default=os.environ.get("SHAL_APPROVE", "gate"),
                    help="gate = reads free, writes need human approval (default); "
                         "auto = free writes (opt-out, recorded in the audit log)")
    args = ap.parse_args(argv)

    # Before any driver op — `--probe` reads too, not just the server (#87, #94).
    # `shal mcp` already came through `shal.cli.main`; the legacy `shal-mcp` script
    # lands straight here, so this entry point makes the same choice explicitly.
    _use_selector_loop_on_win32()
    _import_drivers(args.drivers)
    hal = _resolve_hal(args.topology)
    try:
        bridge = Bridge(hal, free_writes=(args.approve == "auto"))
        if args.probe is not None:                 # one-shot read, no server (#39)
            return _probe(bridge, args.probe or None)

        # Warm transports BEFORE serving so the first MCP read isn't a hang-then-warm
        # (#83): lazy connect/login latency is paid now, not on the client's first call.
        # A bus that can't come up is a friendly stderr warning, not a dead server.
        for path, err in hal.warm():
            print(f"shal-mcp: warning: {path} not ready yet "
                  f"({type(err).__name__}: {err}) — reads to it may be slow until it connects",
                  file=sys.stderr)

        server, stdio_server = _build_server(bridge)

        async def _run() -> None:
            async with stdio_server() as (read, write):
                await server.run(read, write, server.create_initialization_options())

        asyncio.run(_run())
    finally:
        hal.close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
