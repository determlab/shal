"""Route set and failure policy (#235, routes M1 part 2): one route at a time, sticky,
and a changing op is never re-fired on another route after delivered="unknown".

Every test runs on two sim buses, `r1` (the main route) and `r2` (a jump), driven
by their `fail_next` / `fail_delivered_unknown` hooks; a counter on each bus's
`txn` records the traffic each route saw."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import shal
from shal import registry
from shal.buses.sim import SimSensorModel, sim_model
from shal.errors import HopError
from shal.log import current_txn
from shal.routes import RouteSet
from shal.transport import ByteTransport, Read, Write


@sim_model("test,route-probe")
class _ProbeModel(SimSensorModel):
    pass


class RouteProbe(shal.Driver):
    """A device with a retryable read, a plain read and an actuator op."""

    compatible = "test,route-probe"
    kind = ByteTransport

    @shal.idempotent
    @shal.op("Read the value.", side_effect="none")
    def read(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return int.from_bytes(raw, "big", signed=True) / 100

    @shal.op("Read the value once; not marked idempotent.", side_effect="none")
    def peek(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return int.from_bytes(raw, "big", signed=True) / 100

    @shal.op("Move the setpoint.", side_effect="actuator")
    def fire(self, c: float) -> None:
        self.bus.txn(self.addr, [Write(b"\x01" + int(c * 100).to_bytes(2, "big"))])


registry.register(RouteProbe)

_TOPO = """\
shal_version: 1
root:
  r1:
    driver: shal,sim-i2c
    address: sim0
    children:
      board:
        id: board
        driver: test,route-probe
        address: 0x48
        routes:
          - {via: /r2, address: 0x49}
  r2:
    driver: shal,sim-i2c
    address: sim1
    children:
      twin: {driver: "test,route-probe", address: 0x49}   # r2's device at 0x49
"""


class _Rig:
    def __init__(self, hal):
        self.hal = hal
        self.board = hal.get_device("board")
        self.r1 = hal.get_node("board").parent.driver
        self.r2 = hal.get_node("board").routes[1][1].driver
        self.sent = {"r1": 0, "r2": 0}  # txn calls each route's bus saw
        self.unknown = {"r1": 0, "r2": 0}  # scripted delivered="unknown" failures
        for name, bus in (("r1", self.r1), ("r2", self.r2)):
            self._count(name, bus)

    def _count(self, name, bus):
        real = bus.txn

        def txn(addr, ops):
            self.sent[name] += 1
            if self.unknown[name] > 0:
                self.unknown[name] -= 1
                raise HopError("reply lost", path=bus.host.path, hop="sim-i2c",
                               txn=current_txn.get(), delivered="unknown")
            return real(addr, ops)
        bus.txn = txn  # the route set calls bus.txn: this instance attribute

    def writes(self, bus) -> int:
        return bus.model_for(0x48 if bus is self.r1 else 0x49).target_writes


@pytest.fixture
def rig(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(_TOPO, encoding="utf-8")
    with shal.load(p) as hal:
        yield _Rig(hal)


def test_a_routed_node_binds_a_route_set_of_its_kind(rig):
    bus = rig.board.bus
    assert isinstance(bus, RouteSet) and bus.kinds() == frozenset({ByteTransport})
    assert bus.names == ["r1", "r2"] and bus.active == "r1"
    assert rig.board.addr == 0x48  # the driver sees one address, the main one
    assert isinstance(rig.board.read(), float)
    assert rig.sent == {"r1": 1, "r2": 0}


# ---- 3a: delivered="no" -> revive the same route once, then move (any label) -------

def test_one_drop_revives_the_same_route(rig):
    rig.r1.fail_next = 1
    assert isinstance(rig.board.peek(), float)
    assert rig.sent == {"r1": 2, "r2": 0} and rig.board.bus.active == "r1"


def test_exit_1_a_none_op_moves_to_r2_when_r1_fails_with_delivered_no(rig):
    rig.r1.fail_next = 2                       # the revive fails too
    assert isinstance(rig.board.read(), float)
    assert rig.sent == {"r1": 2, "r2": 1}
    assert rig.board.bus.active == "r2"


def test_an_actuator_op_with_r1_refusing_succeeds_on_r2_and_asks_once(rig):
    asked = []

    class _Count(shal.Approver):
        def approve(self, request):
            asked.append(request.op)
            return True

    rig.r1.fail_next = 2
    with shal.approver(_Count()):
        rig.board.fire(30)
    assert asked == ["fire"]                   # one approval, before any I/O
    assert rig.writes(rig.r2) == 1 and rig.writes(rig.r1) == 0


def test_a_denied_actuator_op_sends_nothing_on_any_route(rig):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            rig.board.fire(30, via="r2")
    assert rig.sent == {"r1": 0, "r2": 0}


# ---- 3b: delivered="unknown" ---------------------------------------------------------

def test_exit_2_an_actuator_op_with_delivered_unknown_on_r1_stops(rig):
    rig.r1.fail_delivered_unknown = True
    with pytest.raises(HopError) as excinfo:
        rig.board.fire(30)
    e = excinfo.value
    assert e.via == "r1" and e.delivered == "unknown"
    assert "via=r1" in str(e)
    assert e.fix == ('confirm on another route before re-sending: read board back '
                     'with via="r2"')
    assert rig.sent == {"r1": 1, "r2": 0}      # no retry on r1, no traffic on r2
    assert rig.writes(rig.r2) == 0


def test_a_plain_read_with_delivered_unknown_stops_too(rig):
    rig.unknown["r1"] = 1
    with pytest.raises(HopError) as excinfo:
        rig.board.peek()                       # not @idempotent: no retry, no move
    assert excinfo.value.via == "r1"
    assert rig.sent == {"r1": 1, "r2": 0}


def test_an_idempotent_op_with_delivered_unknown_retries_once_on_r1(rig):
    rig.unknown["r1"] = 1
    assert isinstance(rig.board.read(), float)
    assert rig.sent == {"r1": 2, "r2": 0} and rig.board.bus.active == "r1"


def test_an_idempotent_op_with_delivered_unknown_twice_then_moves(rig):
    rig.unknown["r1"] = 2
    assert isinstance(rig.board.read(), float)
    assert rig.sent == {"r1": 2, "r2": 1} and rig.board.bus.active == "r2"


# ---- sticky ------------------------------------------------------------------------

def test_sticky_after_a_move_and_back_to_main_after_close(rig):
    rig.r1.fail_next = 2
    rig.board.read()                           # moves to r2
    assert rig.sent == {"r1": 2, "r2": 1}
    rig.board.read()                           # r2 first: r1 is not tried
    assert rig.sent == {"r1": 2, "r2": 2}
    rig.board.bus.close()                      # the close/ensure_ready cycle
    assert rig.board.bus.active == "r1"
    rig.board.read()
    assert rig.sent == {"r1": 3, "r2": 2}


# ---- pin by name (3i) --------------------------------------------------------------

def test_a_pinned_call_runs_on_its_route_and_is_not_sticky(rig):
    assert isinstance(rig.board.read(via="r2"), float)
    assert rig.sent == {"r1": 0, "r2": 1} and rig.board.bus.active == "r1"


def test_a_pinned_call_never_moves_and_names_its_route(rig):
    rig.r2.fail_next = 2
    with pytest.raises(HopError) as excinfo:
        rig.board.read(via="r2")
    assert excinfo.value.via == "r2" and "via=r2" in str(excinfo.value)
    assert rig.sent == {"r1": 0, "r2": 2}      # revived once on r2, never tried r1


def test_an_unknown_route_name_lists_the_valid_ones(rig):
    with pytest.raises(shal.Error) as excinfo:
        rig.board.read(via="r3")
    assert str(excinfo.value) == "/r1/board: no route named 'r3'; routes: r1, r2"
    assert not isinstance(excinfo.value, HopError)
    assert rig.sent == {"r1": 0, "r2": 0}


def test_pinning_is_not_gated(rig):
    with shal.approver(shal.DenyAll()):
        assert isinstance(rig.board.read(via="r2"), float)


# ---- 3e: all routes down -------------------------------------------------------------

def test_all_routes_down_is_one_error_listing_each_route(rig):
    rig.r1.fail_next = 2
    rig.r2.fail_next = 2
    with pytest.raises(HopError) as excinfo:
        rig.board.fire(30)
    e = excinfo.value
    text = str(e)
    assert e.delivered == "no" and e.path == "/r1/board"
    # RFC-001 "Failures the agent must be able to read" (#236): no single route to
    # name, every route and its reason, and a fix
    assert e.via is None
    assert text.startswith("/r1/board  no route delivered — r1 via /r1: simulated "
                           "link drop before send; r2 via /r2: simulated link drop "
                           "before send")
    assert e.fix == 'check the wiring sheet for board, or pin a route: via="r1"'
    assert text.endswith(f"Fix: {e.fix}")
    assert rig.writes(rig.r1) == 0 and rig.writes(rig.r2) == 0


# ---- call_tool returns via (#236) ----------------------------------------------------

def test_call_tool_returns_the_route_that_carried_it(rig):
    rig.r1.fail_next = 2
    out = rig.hal.call_tool("board__read")
    assert out["ok"] is True and out["via"] == "r2"


def test_call_tool_failure_has_delivered_via_and_fix(rig):
    rig.r1.fail_delivered_unknown = True
    out = rig.hal.call_tool("board__fire", {"c": 30})
    assert out["ok"] is False and out["delivered"] == "unknown" and out["via"] == "r1"
    assert "via=r1" in out["error"] and "Fix:" not in out["error"]
    assert out["fix"] == ('confirm on another route before re-sending: read board '
                          'back with via="r2"')


def test_call_tool_all_routes_down_is_the_rfc_shape(rig):
    rig.r1.fail_next = 2
    rig.r2.fail_next = 2
    out = rig.hal.call_tool("board__read")
    assert out == {
        "ok": False,
        "error": "/r1/board: no route delivered — r1 via /r1: simulated link drop "
                 "before send; r2 via /r2: simulated link drop before send",
        "delivered": "no", "via": None,
        "fix": 'check the wiring sheet for board, or pin a route: via="r1"',
        # identity (#347 round 2): board's own id/address; RouteProbe itself
        # never declares `simulated` (it is a test-only fake device, not a
        # SHAL simulator), but it runs on `shal,sim-i2c` buses, which now
        # declare `simulated = True` themselves — a real driver on a sim bus
        # for local testing is still a sim reading, OR'd in by `call_identity`
        "device": "board", "address": 0x48, "simulated": True}


def test_call_tool_on_a_node_without_routes_has_no_via_key(rig):
    assert "via" not in rig.hal.call_tool("r2_twin__read")
    rig.r2.fail_next = 2
    out = rig.hal.call_tool("r2_twin__read")
    assert out["ok"] is False and "via" not in out and "fix" not in out


# ---- HopError.via --------------------------------------------------------------------

def test_hop_error_text_is_unchanged_without_via():
    e = HopError("x", path="/p", hop="h", txn="t", delivered="no")
    assert e.via is None
    assert str(e) == "/p  x   (hop: h, txn=t, delivered=no)"
    assert str(e.with_via("r1")) == "/p  x   (hop: h, txn=t, delivered=no, via=r1)"


def test_an_op_parameter_named_via_is_refused_on_a_routed_node(tmp_path):
    class ViaParam(RouteProbe):
        compatible = "test,route-via-param"

        @shal.op("Uses via.", side_effect="none")
        def go(self, via: str) -> str:
            return via

    registry.register(ViaParam)
    p = tmp_path / "t.yaml"
    p.write_text(_TOPO.replace("driver: test,route-probe", "driver: test,route-via-param"),
                 encoding="utf-8")
    with pytest.raises(shal.LoadError, match="op go has a parameter named via"):
        shal.load(p)


# ---- agent path: `shal call` on a two-route sim with r1 refusing ----------------------

_REFUSING = textwrap.dedent('''\
    from shal import register
    from shal.buses.sim import SimI2cBus

    @register
    class RefusingI2c(SimI2cBus):
        compatible = "test,refusing-i2c"

        def __init__(self, node):
            super().__init__(node)
            self.fail_next = 10**6   # every connection refused (delivered="no")
    ''')


def test_agent_path_shal_call_is_carried_by_r2_when_r1_refuses(tmp_path):
    (tmp_path / "refusing.py").write_text(_REFUSING, encoding="utf-8")
    (tmp_path / "t.yaml").write_text(
        _TOPO.replace("driver: test,route-probe", "driver: shal,sim-sensor")
             .replace('driver: "test,route-probe"', 'driver: "shal,sim-sensor"')
             .replace("driver: shal,sim-i2c\n    address: sim0",
                      "driver: test,refusing-i2c\n    address: sim0"),
        encoding="utf-8")
    # the subprocess runs the same shal this test imported
    env = {**os.environ, "PYTHONPATH": str(Path(shal.__file__).parents[1])}
    r = subprocess.run([sys.executable, "-m", "shal.cli", "call", "t.yaml", "board",
                        "read_celsius", "--json", "--drivers", "refusing.py"],
                       cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
                       timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and isinstance(out["result"], float)
