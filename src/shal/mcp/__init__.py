"""SHAL's MCP front door (issues #25/#26/#27).

`Bridge` (here) is the pure, testable core: it turns a loaded `Hal` into MCP tool
definitions and dispatches calls, with host-agnostic in-band approval and a
free-writes toggle. It has **no** dependency on the `mcp` SDK. `server` is the
thin stdio shell that imports `mcp` (the optional `pyshal[mcp]` extra) only when
the `shal-mcp` command actually runs.
"""
from .bridge import APPROVE_TOOL, DENY_TOOL, Bridge

__all__ = ["Bridge", "APPROVE_TOOL", "DENY_TOOL"]
