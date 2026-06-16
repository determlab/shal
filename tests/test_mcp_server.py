"""MCP stdio shell (issue #25). Smoke-tests that the bridge maps cleanly onto the
real `mcp` SDK types — skipped where the optional extra isn't installed."""
import pytest

pytest.importorskip("mcp")  # the optional `pyshal[mcp]` extra

import shal  # noqa: E402
from shal.mcp import Bridge  # noqa: E402
from shal.mcp.server import _build_server  # noqa: E402

_YAML = ("shal_version: 1\n"
         "root:\n"
         "  rig: {id: rig, driver: 'test,mcp-rig', address: 1}\n")


def _hal(tmp_path):
    # reuse the rig registered by test_mcp_bridge
    import tests.test_mcp_bridge  # noqa: F401  (registers test,mcp-rig)
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    return shal.load(p)


def test_tool_defs_map_to_mcp_tool_types(tmp_path):
    import mcp.types as types
    with _hal(tmp_path) as hal:
        server, stdio_server = _build_server(Bridge(hal))
        # the handlers are registered; build the Tool objects the same way the
        # list_tools handler does, to prove every def is a valid MCP Tool
        tools = [types.Tool(name=d["name"], description=d["description"],
                            inputSchema=d["input_schema"],
                            annotations=types.ToolAnnotations(**(d.get("annotations") or {})))
                 for d in Bridge(hal).tool_defs()]
    names = {t.name for t in tools}
    assert "rig__move" in names and "shal_approve" in names
    move = next(t for t in tools if t.name == "rig__move")
    assert move.annotations.destructiveHint is True
