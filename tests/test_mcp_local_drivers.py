"""shal-mcp --drivers: import local/unpackaged drivers before binding (issue #47).

No `mcp` SDK needed — `_import_drivers` / `_resolve_hal` live below the lazy SDK
import, so this exercises the bring-your-own-driver load path directly.
"""
import pytest

import shal
from shal.mcp import server

# A minimal root driver that self-registers on import (the out-of-tree path).
_DRIVER_SRC = """
from shal import Driver, idempotent, op, registry

@registry.register
class LocalThing(Driver):
    compatible = "local,thing-{tag}"
    kind = None
    llm_ready = True

    def bind(self, node):
        super().bind(node)

    @idempotent
    @op("Read a constant.", side_effect="none")
    def read(self) -> int:
        return 42
"""


def _topo(tag: str) -> dict:
    return {"shal_version": 1,
            "root": {"x": {"id": "x", "driver": f"local,thing-{tag}", "address": "a"}}}


def test_import_a_local_driver_file_then_load(tmp_path):
    f = tmp_path / "mydriver.py"           # unique module stem (importlib caches by name)
    f.write_text(_DRIVER_SRC.format(tag="file"), encoding="utf-8")
    server._import_drivers([str(f)])       # registers local,thing-file
    with shal.load(_topo("file")) as hal:
        assert hal.get_device("x").read() == 42


def test_import_a_directory_of_drivers(tmp_path):
    d = tmp_path / "drivers"
    d.mkdir()
    (d / "thing.py").write_text(_DRIVER_SRC.format(tag="dir"), encoding="utf-8")
    (d / "_helper.py").write_text("BROKEN = (\n", encoding="utf-8")  # _-prefixed: skipped
    server._import_drivers([str(d)])
    with shal.load(_topo("dir")) as hal:
        assert hal.get_device("x").read() == 42


def test_missing_drivers_path_is_a_clean_error(tmp_path):
    with pytest.raises(SystemExit, match="not found"):
        server._import_drivers([str(tmp_path / "nope.py")])


def test_unresolved_compatible_points_at_drivers_flag(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("shal_version: 1\nroot:\n"
                 "  x: {id: x, driver: 'nobody,here', address: a}\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="--drivers"):
        server._resolve_hal(str(f))
