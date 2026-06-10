"""YAML `use:` includes — reuse a board topology without copy-paste."""
import textwrap
from pathlib import Path

import pytest

import shal


def write(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


BOARD = """\
shal_version: 1
template:
  driver: shal,sim-i2c
  address: "${busname}"
  children:
    temp0:
      id: "${inst}_temp"
      driver: ti,tmp102
      address: 0x48
"""


def test_use_includes_and_parameterizes(tmp_path):
    write(tmp_path / "board.yaml", BOARD)
    setup = write(tmp_path / "setup.yaml", """
        shal_version: 1
        root:
          board_a:
            use: board.yaml
            with: { inst: a, busname: sim0 }
          board_b:
            use: board.yaml
            with: { inst: b, busname: sim1 }
    """)
    with shal.load(setup) as hal:
        a = hal.get_device("a_temp")     # ids namespaced by the `with:` param
        b = hal.get_device("b_temp")
        assert a is not b
        assert a.read_celsius() == pytest.approx(25.0, abs=0.07)
        # the two includes are independent buses (no shared state)
        assert hal.get_node("a_temp").path == "/board_a/temp0"
        assert hal.get_node("b_temp").path == "/board_b/temp0"


def test_use_site_overrides_template(tmp_path):
    write(tmp_path / "board.yaml", BOARD)
    setup = write(tmp_path / "setup.yaml", """
        shal_version: 1
        root:
          board_a:
            use: board.yaml
            with: { inst: a, busname: sim0 }
            address: sim9          # override the template's address at the use site
    """)
    with shal.load(setup) as hal:
        assert hal.get_device("/board_a").addr == "sim9"   # use-site override wins


def test_cycle_is_caught(tmp_path):
    write(tmp_path / "a.yaml", """
        shal_version: 1
        template: { use: b.yaml }
    """)
    write(tmp_path / "b.yaml", """
        shal_version: 1
        template: { use: a.yaml }
    """)
    setup = write(tmp_path / "setup.yaml", """
        shal_version: 1
        root:
          x: { use: a.yaml }
    """)
    with pytest.raises(shal.LoadError, match="circular use"):
        shal.load(setup)


def test_escape_above_root_is_rejected(tmp_path):
    setup = write(tmp_path / "setup.yaml", """
        shal_version: 1
        root:
          x: { use: ../../secret.yaml }
    """)
    with pytest.raises(shal.LoadError, match="escapes the topology root"):
        shal.load(setup)


def test_missing_template_file(tmp_path):
    setup = write(tmp_path / "setup.yaml", """
        shal_version: 1
        root:
          x: { use: nope.yaml }
    """)
    with pytest.raises(shal.LoadError, match="not found"):
        shal.load(setup)
