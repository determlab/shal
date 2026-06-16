"""shal-mcp --probe: one-shot human-runnable read — no server, no `mcp` SDK (issue #39)."""
import json

import pytest

import shal
from shal.mcp import Bridge, server

_DRIVER = """
from shal import Driver, idempotent, op, registry

@registry.register
class ProbeRig(Driver):
    compatible = "local,probe-rig"
    kind = None
    llm_ready = True

    def bind(self, node):
        super().bind(node)

    @idempotent
    @op("Read the temperature.", side_effect="none")
    def temp(self) -> int:
        return 21

    @op("Move the arm.", side_effect="actuator")
    def move(self, dx: int) -> str:
        return f"moved {dx}"
"""

_TOPO = {"shal_version": 1,
         "root": {"dev": {"id": "dev", "driver": "local,probe-rig", "address": "a"}}}


@pytest.fixture
def bridge(tmp_path):
    f = tmp_path / "probe_drv.py"           # unique module stem
    f.write_text(_DRIVER, encoding="utf-8")
    server._import_drivers([str(f)])
    with shal.load(_TOPO) as hal:
        yield Bridge(hal)


def test_probe_snapshot_prints_a_real_read(bridge, capsys):
    rc = server._probe(bridge, None)
    out = capsys.readouterr().out
    assert rc == 0
    assert "dev__temp: 21" in out                    # a real read landed
    assert "not run by --probe" in out and "dev__move" in out  # write listed, never run
    assert "moved" not in out                        # the actuator was NOT executed


def test_probe_named_read_prints_value(bridge, capsys):
    rc = server._probe(bridge, "dev__temp")
    assert rc == 0
    assert json.loads(capsys.readouterr().out.strip()) == 21


def test_probe_refuses_a_write(bridge):
    with pytest.raises(SystemExit, match="reads only"):
        server._probe(bridge, "dev__move")


def test_probe_unknown_tool_is_clean(bridge):
    with pytest.raises(SystemExit, match="no tool"):
        server._probe(bridge, "dev__nope")
