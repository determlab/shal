"""Friendly cold-user errors for `shal mcp` / `shal-mcp` (issue #71).

A missing topology file and a missing `[mcp]` extra are the two walls a cold user
hits; both must give a friendly message, not a raw traceback.
"""
import sys

import pytest

from shal.mcp import server


def test_missing_topology_file_is_friendly():
    with pytest.raises(SystemExit, match="not found"):
        server._resolve_hal("./__no_such_topology__.yaml")


def test_serving_without_mcp_extra_is_friendly(monkeypatch):
    # simulate the optional `mcp` SDK not being installed: importing it raises
    for name in ("mcp", "mcp.types", "mcp.server", "mcp.server.stdio"):
        monkeypatch.setitem(sys.modules, name, None)
    with pytest.raises(SystemExit, match=r"pyshal\[mcp\]"):
        server._build_server(None)  # fails at the import, before the bridge is used
