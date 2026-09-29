"""Routes M1 exit tests (#238, routes M1 part 5), end to end on the sim.

Six checks, then the agent path. NOTE: the RFC's exit-test table was not available
when this file was written; the six were inferred from the routes implementation
(#234-#237) and its tests:

1. `shal tools --json` lists a routed node once, with its two routes (and `via` enum).
2. `shal call --via <name>` runs on each route, and the result says which.
3. An unknown `--via` name is refused before any I/O and the error lists the valid ones.
4. A changing op fails over to the next route on delivered="no", approved once.
5. A changing op is never re-sent on another route after delivered="unknown".
6. `shal routes --json` prints the declared routes in order.

The agent path parses the `routes:` example out of AGENTS.md and runs it as written."""
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


# ---- the CLI checks, on the topology AGENTS.md ships ---------------------------------

@pytest.fixture
def lab(tmp_path):
    (tmp_path / "routes.yaml").write_text(_agents_md_routes_example(), encoding="utf-8")
    return tmp_path


def test_exit_1_tools_lists_the_routed_node_once_with_two_routes(lab):
    r = _shal("tools", "routes.yaml", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    tools = json.loads(r.stdout)["tools"]
    assert {t["device"] for t in tools if t["device"] == "ambient_temp"} == {"ambient_temp"}
    assert [t["device"] for t in tools].count("twin") == 0  # the jump's own node is
    routed = [t for t in tools if "routes" in t]           # not a second listing
    assert routed and all(t["device"] == "ambient_temp" for t in routed)
    assert all(t["routes"] == ["bench", "jump"] for t in routed)
    assert len({t["op"] for t in routed}) == len(routed)   # each op once
    assert all(t["input_schema"]["properties"]["via"]["enum"] == ["bench", "jump"]
               for t in routed)


@pytest.mark.parametrize("route", ["bench", "jump"])
def test_exit_2_call_via_runs_on_each_route(lab, route):
    r = _shal("call", "routes.yaml", "ambient_temp", "read_celsius", "--via", route,
              "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["via"] == route
    assert isinstance(out["result"], float)


def test_exit_3_unknown_via_is_refused_and_lists_the_valid_names(lab):
    r = _shal("call", "routes.yaml", "ambient_temp", "read_celsius", "--via", "wifi",
              "--json", cwd=lab)
    assert r.returncode == 3
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["routes"] == ["bench", "jump"]
    assert "bench" in out["error"] and "jump" in out["error"]


def test_exit_6_routes_prints_the_declared_routes_in_order(lab):
    r = _shal("routes", "routes.yaml", "ambient_temp", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["routes"] == [
        {"name": "bench", "via": "/bench", "address": 0x48},
        {"name": "jump", "via": "/jump", "address": 0x49}]


# ---- the failure policy, in process on two sim buses ---------------------------------

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


def test_exit_4_a_changing_op_fails_over_on_delivered_no_and_asks_once(hal):
    r1, r2 = _buses(hal)
    r1.fail_next = 2                       # refused, and the revive is refused too
    ask = _Count()
    with shal.approver(ask):
        out = hal.call_tool("board__fire", {"c": 30})
    assert out["ok"] is True and out["via"] == "r2"
    assert ask.asked == ["fire"]           # approved once, before any I/O
    assert r2.model_for(0x49).target_writes == 1
    assert r1.model_for(0x48).target_writes == 0


def test_exit_5_a_changing_op_is_not_re_sent_after_delivered_unknown(hal):
    r1, r2 = _buses(hal)
    r1.fail_delivered_unknown = True       # the request left; the reply was lost
    with shal.approver(_Count()):
        out = hal.call_tool("board__fire", {"c": 30})
    assert out["ok"] is False and out["delivered"] == "unknown"
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
    routed = [t for t in json.loads(r.stdout)["tools"] if "routes" in t]
    tool = next(t for t in routed if t["kind"] == "read")
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
