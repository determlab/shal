"""`routes:` at load (#231, routes M1 part 1): a main route from the node's place
in the tree, named jumps to other buses, and the four load refusals."""
import textwrap
from pathlib import Path

import pytest

import shal


def write(p: Path, body: str) -> Path:
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


def topo(tmp_path, routes: str, extra: str = "") -> Path:
    """`board` under `bus` (its main route), with `routes:` and more root nodes."""
    return write(tmp_path / "t.yaml", f"""
        shal_version: 1
        root:
          bus:
            driver: shal,sim-i2c
            address: sim0
            children:
              board:
                id: board
                driver: shal,sim-sensor
                address: 0x48
                routes:
{textwrap.indent(textwrap.dedent(routes).strip(), " " * 18)}
          bus2:
            driver: shal,sim-i2c
            address: sim1
{textwrap.indent(textwrap.dedent(extra).strip(), " " * 10)}
    """)


def refused(path: Path) -> str:
    with pytest.raises(shal.LoadError) as excinfo:
        shal.load(path)
    return str(excinfo.value)


# ---- a routed node loads ---------------------------------------------------------

def test_two_route_node_loads_with_one_id(tmp_path):
    p = topo(tmp_path, "- {via: /bus2, address: 0x49}")  # /bus2 is declared after
    with shal.load(p) as hal:
        node = hal.get_node("board")
        assert node.path == "/bus/board"
        assert [r[0] for r in node.routes] == ["bus", "bus2"]
        main, jump = node.routes
        assert main == ("bus", node.parent, 0x48)
        assert (jump[1].path, jump[2]) == ("/bus2", 0x49)
        assert node.parent.routes == []  # an unrouted node holds none
        # reads on its main route (the failure policy: test_routes_policy.py)
        assert isinstance(hal.get_device("board").read_celsius(), float)


def test_user_set_route_name(tmp_path):
    p = topo(tmp_path, "- {via: /bus2, address: 0x49, name: backup}")
    with shal.load(p) as hal:
        assert [r[0] for r in hal.get_node("board").routes] == ["bus", "backup"]


# ---- the four refusals (RFC §1) ------------------------------------------------------

def test_routed_node_with_no_parent_bus_is_refused(tmp_path):
    p = write(tmp_path / "t.yaml", """
        shal_version: 1
        root:
          board:
            driver: shal,sim-sensor
            address: 0x48
            routes:
              - {via: /ssh, address: 0x48}
          ssh: {driver: "shal,sim-i2c", address: sim0}
    """)
    assert refused(p) == ("/board: a node with routes must sit under its main bus; "
                          "put it there and list the other channels as routes")


def test_routed_node_under_a_non_bus_parent_is_refused(tmp_path):
    p = write(tmp_path / "t.yaml", """
        shal_version: 1
        root:
          shelf:
            address: shelf0
            children:
              board:
                driver: shal,sim-sensor
                address: 0x48
                routes:
                  - {via: /ssh, address: 0x48}
          ssh: {driver: "shal,sim-i2c", address: sim0}
    """)
    assert refused(p) == ("/shelf/board: a node with routes must sit under its main "
                          "bus; put it there and list the other channels as routes")


def test_unresolved_via_is_refused(tmp_path):
    p = topo(tmp_path, "- {via: /ssh, address: 0x48}")
    assert refused(p) == "/bus/board: route ssh: no bus at /ssh"


def test_via_at_a_node_that_is_not_a_bus_is_refused(tmp_path):
    p = topo(tmp_path, "- {via: /other/t, address: 0x48}", """
        other:
          driver: shal,sim-i2c
          address: sim2
          children:
            t: {driver: "shal,sim-sensor", address: 0x4a}
    """)
    assert refused(p) == "/bus/board: route t: no bus at /other/t"


def test_duplicate_route_name_is_refused(tmp_path):
    p = topo(tmp_path, "- {via: /bus2, address: 0x49, name: bus}")
    assert refused(p) == ("/bus/board: route bus via /bus and route bus via /bus2 "
                          "share a name; set name: on one of them")


def test_jump_to_a_bus_of_the_wrong_kind_is_refused(tmp_path):
    p = topo(tmp_path, "- {via: /msg, address: 0x48}",
             'msg: {driver: "shal,sim-msg", address: sim}')
    assert refused(p) == ("/bus/board: route msg via /msg offers MessageTransport, "
                          "driver shal,sim-sensor needs ByteTransport")


def test_jump_address_outside_the_bus_grammar_is_refused_at_load(tmp_path):
    # the same grammar check the main route gets at bind (CTO ruling on PR #242)
    p = topo(tmp_path, "- {via: /bus2, address: 0x99}")
    assert refused(p) == ("/bus/board: route bus2 via /bus2: sim-i2c: invalid 7-bit "
                          "I2C address '153' (grammar: 0x03-0x77)")


def test_routes_without_address_stays_invalid(tmp_path):
    p = write(tmp_path / "t.yaml", """
        shal_version: 1
        root:
          bus:
            driver: shal,sim-i2c
            address: sim0
            children:
              board:
                driver: shal,sim-sensor
                routes:
                  - {via: /bus, address: 0x48}
    """)
    assert "root/bus/children/board' has routes but no address" in refused(p)


# ---- use: templates -----------------------------------------------------------------

_SETUP = """
    shal_version: 1
    root:
      bus:
        driver: shal,sim-i2c
        address: sim0
        children:
          board:
            use: board.yaml
            with: {rack: /rack}
      rack:
        driver: shal,sim-i2c
        address: sim1
        children:
          console2: {driver: "shal,sim-i2c", address: sim2}
"""


def test_template_with_parameterised_via_loads(tmp_path):
    write(tmp_path / "board.yaml", """
        shal_version: 1
        template:
          id: board
          driver: shal,sim-sensor
          address: 0x48
          routes:
            - {via: "${rack}/console2", address: 0x49}
    """)
    with shal.load(write(tmp_path / "t.yaml", _SETUP)) as hal:
        routes = hal.get_node("board").routes
        assert [(r[0], r[1].path) for r in routes] == [("bus", "/bus"),
                                                       ("console2", "/rack/console2")]


def test_template_with_literal_via_is_refused(tmp_path):
    write(tmp_path / "board.yaml", """
        shal_version: 1
        template:
          id: board
          driver: shal,sim-sensor
          address: 0x48
          routes:
            - {via: /rack/console2, address: 0x49}
    """)
    assert refused(write(tmp_path / "t.yaml", _SETUP)) == (
        "node 'board': use 'board.yaml': route console2 via /rack/console2 is a "
        "literal path; a via inside a template must be a parameter "
        "(e.g. ${rack}/console2, set from with:)")
