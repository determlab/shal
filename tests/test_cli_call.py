"""`shal call` — run one op from the command line (shal#160, ADK R11).

A `none`/`write` op runs and prints its result; a `config`/`actuator` op is refused
from its declared label before it is invoked: exit 2, nothing sent, and the message
names the label and both ways to approve. Exit 3 is "could not run" (a mistake),
kept apart from 2 so an agent can tell a refusal from an error. Most tests run the
real command in a fresh process and check the real exit code.
"""
import json
import subprocess
import sys
import textwrap

import pytest

from shal import cli
from shal.buses import sim as sim_mod

_SIM_YAML = textwrap.dedent("""\
    shal_version: 1
    root:
      bus:
        driver: shal,sim-i2c
        address: sim0
        children:
          temp0:
            id: ambient_temp
            driver: shal,sim-sensor
            address: 0x48
    """)

# A local driver (loaded with --drivers) whose ops leave a mark in a file named by
# the node's address: that file is how a SEPARATE process proves an op ran or not.
_MARKER_DRIVER = textwrap.dedent('''\
    import json
    import pathlib

    from shal import Driver, idempotent, op, register

    @register
    class Marker(Driver):
        compatible = "test,call-marker"
        kind = None
        llm_ready = True

        def _mark(self, what):
            p = pathlib.Path(self.addr)
            p.write_text((p.read_text() if p.exists() else "") + what + "\\n")

        @idempotent
        @op("Read the level.", side_effect="none")
        def level(self) -> int:
            return 7

        @op("Set the level.", side_effect="write",
            params={"n": {"minimum": 0, "maximum": 100}})
        def set_level(self, n: int, loud: bool = False, tag: str = "x",
                      gain: float = 1.0) -> dict:
            self._mark(f"set_level {n}")
            return {"n": n, "loud": loud, "tag": tag, "gain": gain}

        @op("Clear the level.", side_effect="write")
        def clear(self) -> None:
            self._mark("clear")

        @op("Arm the output.", side_effect="actuator")
        def arm(self) -> None:
            self._mark("arm")

        @op("Move by some steps.")  # no label: inferred actuator, fail-closed
        def move(self, steps: int) -> None:
            self._mark(f"move {steps}")

        @idempotent
        @op("Go to an absolute position.")  # no label: actuator too (#194)
        def goto(self, pos: int) -> None:
            self._mark(f"goto {pos}")
    ''')

_MARKER_YAML = ("shal_version: 1\n"
                "root:\n"
                "  thing: {id: thing, driver: 'test,call-marker', address: marks.txt}\n")


def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    """Run the real command in a fresh process (not an import of `main`)."""
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    (tmp_path / "marker_driver.py").write_text(_MARKER_DRIVER, encoding="utf-8")
    (tmp_path / "marker.yaml").write_text(_MARKER_YAML, encoding="utf-8")
    return tmp_path


def _marker(*argv: str, cwd) -> subprocess.CompletedProcess:
    return _shal("call", "marker.yaml", "thing", *argv,
                 "--drivers", "marker_driver.py", cwd=cwd)


def _marks(lab) -> list[str]:
    p = lab / "marks.txt"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


# ---- a read runs -----------------------------------------------------------------

def test_read_prints_the_value_as_json_and_exits_0(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "read_celsius", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and isinstance(out["result"], float)
    assert out["side_effect"] == "none" and out["tool"] == "ambient_temp__read_celsius"


def test_read_without_json_prints_the_bare_value(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "read_celsius", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert isinstance(json.loads(r.stdout), float)


def test_node_by_path_works_too(lab):
    r = _shal("call", "sim.yaml", "/bus/temp0", "read_celsius", cwd=lab)
    assert r.returncode == 0, r.stderr


# ---- a gated op is refused: exit 2, nothing sent -----------------------------------

def test_config_op_is_refused_with_exit_2_the_label_and_both_ways_to_approve(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "set_target", "30", cwd=lab)
    assert r.returncode == 2
    assert "'config'" in r.stderr
    assert "shal mcp sim.yaml" in r.stderr            # way 1: an MCP host
    assert "shal.approver(" in r.stderr               # way 2: Python
    assert "Nothing was sent" in r.stderr
    assert r.stdout == "" and "Traceback" not in r.stderr


def test_refusal_is_json_too(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "set_target", "30", "--json", cwd=lab)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["rejected"] == "approval"
    assert out["side_effect"] == "config" and out["sent"] is False
    assert any("shal mcp" in w for w in out["approve_with"])
    assert any("shal.approver" in w for w in out["approve_with"])


def test_refused_set_target_never_reaches_the_bus(tmp_path, monkeypatch, capsys):
    """The sim bus is never even activated, and the sensor model sees no txn: the
    refusal is decided from the label, before the op is called."""
    (tmp_path / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    seen: list[str] = []
    monkeypatch.setattr(sim_mod.SimI2cBus, "activate",
                        lambda self: seen.append("activate"))
    monkeypatch.setattr(sim_mod.SimI2cBus, "txn",
                        lambda self, addr, ops: seen.append("bus txn"))
    monkeypatch.setattr(sim_mod.SimSensorModel, "txn",
                        lambda self, ops: seen.append("model txn"))
    rc = cli.main(["call", str(tmp_path / "sim.yaml"), "ambient_temp",
                   "set_target", "30"])
    assert rc == 2
    assert seen == []
    assert "'config'" in capsys.readouterr().err
    # control: the same spies DO see a read reach the bus
    cli.main(["call", str(tmp_path / "sim.yaml"), "ambient_temp", "read_celsius"])
    assert "bus txn" in seen


def test_refused_ops_leave_no_mark_across_processes(lab):
    # an unlabelled @idempotent op is refused too: @idempotent never declares a
    # read (#194)
    for argv in (["arm"], ["move", "3"], ["goto", "3"]):
        r = _marker(*argv, cwd=lab)
        assert r.returncode == 2, (argv, r.stderr)
        assert "'actuator'" in r.stderr
    assert _marks(lab) == []


def test_refusal_comes_before_argument_checks(lab):
    # the label decides first: a gated op is refused, not "bad value"
    r = _shal("call", "sim.yaml", "ambient_temp", "set_target", "hot", cwd=lab)
    assert r.returncode == 2


# ---- the refusal reads the LIVE gated set, like the runtime gate (#114) ------------
# A CLI process seats no policy from code: the operator declares it in the
# topology (`policy: {gated: [...]}`, ADR-001 addendum 5). `shal call` must then
# refuse exactly what the runtime would stop, and run exactly what it would run.
# A --drivers module that tries to seat a policy is refused at load.

def _policy_topology(lab, gated: str) -> str:
    (lab / "policy.yaml").write_text(_MARKER_YAML + f"policy:\n  gated: {gated}\n",
                                     encoding="utf-8")
    return "policy.yaml"


def test_a_widened_topology_policy_makes_shal_call_refuse_a_write(lab):
    topo = _policy_topology(lab, "[write, actuator, config]")
    r = _shal("call", topo, "thing", "set_level", "5", "--json",
              "--drivers", "marker_driver.py", cwd=lab)
    assert r.returncode == 2, r.stderr
    out = json.loads(r.stdout)
    assert out["rejected"] == "approval" and out["side_effect"] == "write"
    assert out["sent"] is False
    assert _marks(lab) == []
    # a read stays free under any policy ("none" cannot be gated)
    r = _shal("call", topo, "thing", "level", "--drivers", "marker_driver.py", cwd=lab)
    assert r.returncode == 0, r.stderr


def test_a_narrowed_topology_policy_lets_shal_call_run_what_the_runtime_runs(lab):
    topo = _policy_topology(lab, "[config]")
    r = _shal("call", topo, "thing", "arm", "--drivers", "marker_driver.py", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert _marks(lab) == ["arm"]


@pytest.mark.parametrize("seat", ['shal.set_gated_effects({"write", "actuator", "config"})',
                                  "shal.driver._DEFAULT_GATED = frozenset()",
                                  "shal.set_approver(shal.AutoApprove())"])
def test_a_drivers_module_that_seats_a_policy_fails_to_load(lab, seat):
    """Round 1 let a --drivers module narrow the set and `shal call` honoured it:
    that is exactly the case ADR-001 addendum 5 forbids. It is a load failure now,
    and nothing runs."""
    (lab / "seating_driver.py").write_text(
        _MARKER_DRIVER + f"\nimport shal\n{seat}\n", encoding="utf-8")
    r = _shal("call", "marker.yaml", "thing", "arm",
              "--drivers", "seating_driver.py", cwd=lab)
    assert r.returncode == 3, r.stderr
    assert "seating_driver changed the approval policy at import" in r.stderr
    assert "Traceback" not in r.stderr
    assert _marks(lab) == []


# ---- a write op runs ---------------------------------------------------------------

def test_write_op_runs_and_its_effect_is_real(lab):
    r = _marker("set_level", "42", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["side_effect"] == "write"
    assert out["result"] == {"n": 42, "loud": False, "tag": "x", "gain": 1.0}
    assert _marks(lab) == ["set_level 42"]


def test_write_op_with_no_result_prints_ok(lab):
    r = _marker("clear", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "ok"
    assert _marks(lab) == ["clear"]


def test_values_are_coerced_from_the_schema_positional_then_named(lab):
    r = _marker("set_level", "0x10", "yes", "gain=2.5", "tag=a=b", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["result"] == {"n": 16, "loud": True, "tag": "a=b",
                                              "gain": 2.5}


def test_a_limit_rejection_is_exit_1_with_the_violation(lab):
    r = _marker("set_level", "500", "--json", cwd=lab)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["rejected"] == "limits"
    assert _marks(lab) == []


# ---- a mistake is exit 3, never 2 ----------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["call", "missing.yaml", "ambient_temp", "read_celsius"],
    ["call", "sim.yaml", "nope", "read_celsius"],
    ["call", "sim.yaml", "ambient_temp", "nope"],
    ["call", "sim.yaml", "ambient_temp"],                        # argparse: missing op
    ["call", "sim.yaml", "ambient_temp", "read_celsius", "5"],   # too many values
    ["call", "sim.yaml", "ambient_temp", "read_celsius", "--approve"],
])
def test_a_mistake_exits_3_not_2(lab, argv):
    r = _shal(*argv, cwd=lab)
    assert r.returncode == 3, r.stderr
    assert "shal call:" in r.stderr and "Traceback" not in r.stderr


@pytest.mark.parametrize("argv", [
    ["call", "missing.yaml", "ambient_temp", "read_celsius"],
    ["call", "sim.yaml", "nope", "read_celsius"],
    ["call", "sim.yaml", "ambient_temp", "nope"],
    ["call", "sim.yaml", "ambient_temp"],
    ["call", "sim.yaml", "ambient_temp", "read_celsius", "5"],
    ["call", "sim.yaml", "ambient_temp", "read_celsius", "--approve"],
])
def test_a_mistake_with_json_is_ok_false_on_stdout(lab, argv):
    r = _shal(*argv, "--json", cwd=lab)
    assert r.returncode == 3, r.stderr
    assert json.loads(r.stdout)["ok"] is False
    assert "shal call:" in r.stderr


@pytest.mark.parametrize("argv", [
    ["set_level", "ten"],          # not an integer
    ["set_level"],                 # missing n
    ["set_level", "n=1", "2"],     # positional after name=value
    ["set_level", "1", "n=2"],     # n given twice
])
def test_bad_values_exit_3(lab, argv):
    r = _marker(*argv, cwd=lab)
    assert r.returncode == 3, r.stderr
    assert _marks(lab) == []


# ---- help ------------------------------------------------------------------------------

def test_help_says_there_is_no_approve_flag_and_where_approval_happens(lab):
    r = _shal("call", "--help", cwd=lab)
    assert r.returncode == 0
    assert "NO --approve flag" in r.stdout
    assert "shal mcp <topology>" in r.stdout and "shal.approver(" in r.stdout
    assert "2 refused" in r.stdout and "3 could not run" in r.stdout


def test_refusal_json_how_to_approve_is_the_same_constant_as_the_denial(lab):
    """#186: `shal call`'s refusal and the no-approver denial share ONE string."""
    from shal.errors import HOW_TO_APPROVE_LINE, NO_APPROVER_MESSAGE
    r = _shal("call", "sim.yaml", "ambient_temp", "set_target", "30", "--json", cwd=lab)
    assert r.returncode == 2
    out = json.loads(r.stdout)
    assert out["how_to_approve"] == HOW_TO_APPROVE_LINE
    assert NO_APPROVER_MESSAGE.endswith(out["how_to_approve"])


# ---- routes: `via` in the JSON (#236, RFC-001 "Agent path") -------------------------

def _two_route_lab(tmp_path, refusing):
    """The two-route sim of #235 (r1 on bus sim0, r2 on bus sim1), `board` a
    shal,sim-sensor; each bus address in `refusing` refuses every connection."""
    from tests.test_routes_policy import _REFUSING, _TOPO
    topo = (_TOPO.replace("driver: test,route-probe", "driver: shal,sim-sensor")
                 .replace('driver: "test,route-probe"', 'driver: "shal,sim-sensor"'))
    for addr in refusing:
        topo = topo.replace(f"driver: shal,sim-i2c\n    address: {addr}",
                            f"driver: test,refusing-i2c\n    address: {addr}")
    (tmp_path / "refusing.py").write_text(_REFUSING, encoding="utf-8")
    (tmp_path / "t.yaml").write_text(topo, encoding="utf-8")
    return tmp_path


def test_agent_path_json_says_r2_carried_it_when_r1_refuses(tmp_path):
    lab = _two_route_lab(tmp_path, ["sim0"])
    r = _shal("call", "t.yaml", "board", "read_celsius", "--json",
              "--drivers", "refusing.py", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and isinstance(out["result"], float)
    assert out["via"] == "r2" and out["side_effect"] == "none"


def test_agent_path_json_when_every_route_refuses_has_delivered_via_and_fix(tmp_path):
    lab = _two_route_lab(tmp_path, ["sim0", "sim1"])
    r = _shal("call", "t.yaml", "board", "read_celsius", "--json",
              "--drivers", "refusing.py", cwd=lab)
    assert r.returncode == 1, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["delivered"] == "no" and out["via"] is None
    assert out["error"] == ("/r1/board: no route delivered — r1 via /r1: simulated "
                            "link drop before send; r2 via /r2: simulated link drop "
                            "before send")
    assert out["fix"] == 'check the wiring sheet for board, or pin a route: via="r1"'


def test_json_of_a_node_without_routes_has_no_via_key(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "read_celsius", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert "via" not in json.loads(r.stdout)


# ---- --via pins a route for one call (#237) --------------------------------------------

def test_via_pins_r2_when_r1_would_carry_it(tmp_path):
    lab = _two_route_lab(tmp_path, [])
    r = _shal("call", "t.yaml", "board", "read_celsius", "--via", "r2", "--json",
              "--drivers", "refusing.py", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["via"] == "r2"


def test_via_r2_down_fails_on_r2_and_never_tries_r1(tmp_path):
    # #237 exit test 3: r1 is healthy, so a call that moved to it would succeed
    lab = _two_route_lab(tmp_path, ["sim1"])
    r = _shal("call", "t.yaml", "board", "read_celsius", "--via", "r2", "--json",
              "--drivers", "refusing.py", cwd=lab)
    assert r.returncode == 1, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["via"] == "r2" and out["delivered"] == "no"
    assert "via=r2" in out["error"]


def test_unknown_via_exits_3_and_lists_the_valid_names(tmp_path):
    lab = _two_route_lab(tmp_path, [])
    r = _shal("call", "t.yaml", "board", "read_celsius", "--via", "r9", "--json",
              "--drivers", "refusing.py", cwd=lab)
    assert r.returncode == 3, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["routes"] == ["r1", "r2"]
    assert out["error"] == "/r1/board: no route named 'r9'; routes: r1, r2"
    assert out["error"] in r.stderr and "Traceback" not in r.stderr


def test_via_on_a_node_without_routes_takes_only_its_main_route(lab):
    r = _shal("call", "sim.yaml", "ambient_temp", "read_celsius", "--via", "bus",
              "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert "via" not in json.loads(r.stdout)  # unchanged: nothing to pin
    r = _shal("call", "sim.yaml", "ambient_temp", "read_celsius", "--via", "ssh",
              "--json", cwd=lab)
    assert r.returncode == 3, r.stderr
    assert json.loads(r.stdout)["routes"] == ["bus"]
