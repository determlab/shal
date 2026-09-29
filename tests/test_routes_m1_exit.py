"""Routes M1 exit tests (#238, routes M1 part 5), end to end on the sim.

RFC-001 ("Milestones", row M1) numbers six exit tests. This file is the one place
that proves all six — reusing the tests from parts 1-4 (#234-#237) where they
already prove the same thing on the sim — plus the DoD's separate agent-path check
(AGENTS.md's own `routes:` example, run as written).

1. A `none` op moves to route 2 on connection refused (delivered="no"), via="r2".
   (An actuator variant is kept too, in-process on this file's own sim rig.)
2. An actuator op with `delivered="unknown"` stops, raising/returning an error that
   names `via="r1"`; route 2 sees no traffic.
3. A pinned `--via r2` never moves; a route that is down raises an error naming it.
4. The four load-time refusals (RFC §1): a jump to a bus of the wrong kind, a
   duplicate route name, a via with no bus at that path, and a routed node with no
   parent bus.
5. `shal tools --json` lists a routed node once, with its routes.
6. Every log line of a routed node's call carries `via`.
"""
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import shal
from shal import registry
from shal.buses.sim import SimSensorModel, sim_model
from shal.errors import HopError
from shal.transport import ByteTransport, Read, Write
from tests import test_loader_routes as _loader_routes
from tests import test_logging as _logging_tests
from tests import test_routes_policy as _policy

# Reused fixtures and tests (#234-#237): bound under this module's own names so
# pytest collects and runs them here too, numbered to the RFC's M1 exit table.
rig = _policy.rig
routed = _logging_tests.routed

test_m1_exit_1_none_op_moves_to_r2_on_delivered_no = \
    _policy.test_exit_1_a_none_op_moves_to_r2_when_r1_fails_with_delivered_no
test_m1_exit_2_actuator_op_with_delivered_unknown_raises_naming_r1_direct = \
    _policy.test_exit_2_an_actuator_op_with_delivered_unknown_on_r1_stops
test_m1_exit_3_pinned_route_down_raises_naming_it = \
    _policy.test_a_pinned_call_never_moves_and_names_its_route

test_m1_exit_4_wrong_kind_jump_is_refused = \
    _loader_routes.test_jump_to_a_bus_of_the_wrong_kind_is_refused
test_m1_exit_4_duplicate_route_name_is_refused = \
    _loader_routes.test_duplicate_route_name_is_refused
test_m1_exit_4_missing_path_is_refused = \
    _loader_routes.test_unresolved_via_is_refused
test_m1_exit_4_parentless_routed_node_is_refused = \
    _loader_routes.test_routed_node_with_no_parent_bus_is_refused

test_m1_exit_6_every_log_line_of_a_routed_node_has_via = \
    _logging_tests.test_exit_6_every_log_line_of_a_routed_node_has_via

_ROOT = Path(__file__).resolve().parents[1]
_ENV = {**os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in (str(_ROOT / "src"),
                                                  os.environ.get("PYTHONPATH")) if p)}


def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd, env=_ENV,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def _agents_md_routes_example() -> str:
    text = (_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
    return next(b for b in blocks if "routes:" in b)


# ---- exit 5: `shal tools --json` lists the routed node once, with its routes --------

@pytest.fixture
def lab(tmp_path):
    (tmp_path / "routes.yaml").write_text(_agents_md_routes_example(), encoding="utf-8")
    return tmp_path


def test_m1_exit_5_tools_lists_the_routed_node_once_with_two_routes(lab):
    r = _shal("tools", "routes.yaml", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    tools = json.loads(r.stdout)["tools"]
    assert {t["device"] for t in tools if t["device"] == "ambient_temp"} == {"ambient_temp"}
    assert [t["device"] for t in tools].count("twin") == 0  # the jump's own node is
    routed_tools = [t for t in tools if "routes" in t]      # not a second listing
    assert routed_tools and all(t["device"] == "ambient_temp" for t in routed_tools)
    assert all(t["routes"] == ["bench", "jump"] for t in routed_tools)
    assert len({t["op"] for t in routed_tools}) == len(routed_tools)   # each op once
    assert all(t["input_schema"]["properties"]["via"]["enum"] == ["bench", "jump"]
               for t in routed_tools)


# ---- exits 1 & 2, actuator variants: an in-process rig of this file's own -----------

@sim_model("test,m1-exit-probe")
class _ProbeModel(SimSensorModel):
    pass


class M1Probe(shal.Driver):
    compatible = "test,m1-exit-probe"
    kind = ByteTransport

    @shal.op("Move the setpoint.", side_effect="actuator")
    def fire(self, c: float) -> None:
        self.bus.txn(self.addr, [Write(b"\x01" + int(c * 100).to_bytes(2, "big"))])

    @shal.idempotent
    @shal.op("Read the value.", side_effect="none")
    def read(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return int.from_bytes(raw, "big", signed=True) / 100


registry.register(M1Probe)

_POLICY_TOPO = """\
shal_version: 1
root:
  r1:
    driver: shal,sim-i2c
    address: sim0
    children:
      board:
        id: board
        driver: test,m1-exit-probe
        address: 0x48
        routes:
          - {via: /r2, address: 0x49, name: r2}
  r2:
    driver: shal,sim-i2c
    address: sim1
    children:
      twin: {driver: "test,m1-exit-probe", address: 0x49}
"""


class _Count(shal.Approver):
    def __init__(self):
        self.asked = []

    def approve(self, request):
        self.asked.append(request.op)
        return True


@pytest.fixture
def hal(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(_POLICY_TOPO, encoding="utf-8")
    with shal.load(p) as h:
        yield h


def _buses(hal):
    node = hal.get_node("board")
    return node.parent.driver, node.routes[1][1].driver


def test_m1_exit_1_actuator_variant_moves_to_r2_on_delivered_no(hal):
    r1, r2 = _buses(hal)
    r1.fail_next = 2                       # refused, and the revive is refused too
    ask = _Count()
    with shal.approver(ask):
        out = hal.call_tool("board__fire", {"c": 30})
    assert out["ok"] is True and out["via"] == "r2"
    assert ask.asked == ["fire"]           # approved once, before any I/O
    assert r2.model_for(0x49).target_writes == 1
    assert r1.model_for(0x48).target_writes == 0


def test_m1_exit_2_actuator_op_with_delivered_unknown_raises_naming_r1(hal):
    r1, r2 = _buses(hal)
    r1.fail_delivered_unknown = True       # the request left; the reply was lost
    with shal.approver(_Count()):
        out = hal.call_tool("board__fire", {"c": 30})
    assert out["ok"] is False and out["delivered"] == "unknown" and out["via"] == "r1"
    assert r2.model_for(0x49).target_writes == 0     # never re-sent on r2
    assert r1.model_for(0x48).target_writes == 0     # nor retried on r1
    # the same through the driver: HopError names the route and the next step
    r1.fail_delivered_unknown = True
    with shal.approver(_Count()), pytest.raises(HopError) as ei:
        hal.get_device("board").fire(30)
    assert ei.value.via == "r1" and ei.value.delivered == "unknown"
    assert r2.model_for(0x49).target_writes == 0


# ---- the agent path: the AGENTS.md example, run as written --------------------------

def test_agent_path_agents_md_routes_example_runs_on_the_sim(tmp_path):
    (tmp_path / "routes.yaml").write_text(_agents_md_routes_example(), encoding="utf-8")
    r = _shal("tools", "routes.yaml", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    routed_tools = [t for t in json.loads(r.stdout)["tools"] if "routes" in t]
    tool = next(t for t in routed_tools if t["kind"] == "read")
    for name in tool["routes"]:
        c = _shal("call", "routes.yaml", tool["device"], tool["op"], "--via", name,
                  "--json", cwd=tmp_path)
        assert c.returncode == 0, c.stderr
        assert json.loads(c.stdout)["via"] == name


def test_agents_md_states_the_three_route_sentences():
    text = " ".join((_ROOT / "AGENTS.md").read_text(encoding="utf-8").split())
    for s in ("A node sits under its main bus.",
              "Extra channels are named jumps in `routes:`.",
              "A changing op is never re-sent on another route after an unknown "
              "delivery."):
        assert s in text
    assert textwrap.dedent(_agents_md_routes_example()).count("routes:") == 1
