"""shal,sim-psu — the sim family's second device (ops#117 CTO ruling 1,
prerequisite C7, #252).

It ships so the v1 story (`psu.set_voltage`) has a device to run on with no
hardware: `set_voltage` is gated (`actuator`, it energizes the simulated
output now), `measure_voltage`/`measure_current` are free reads, and
`measure_current` follows Ohm's law from the set voltage and the node's
configured `load_ohms` — so a topology can model a good unit and a failing one.
"""
import json
import subprocess
import sys
import textwrap

import pytest

import shal
from shal import cli
from shal.buses import sim_scpi as sim_scpi_mod
from shal.conformance import check_driver


def _psu_yaml(load_ohms: float) -> str:
    return textwrap.dedent(f"""\
        shal_version: 1
        root:
          rack:
            id: rack
            driver: shal,sim-scpi
            address: sim0
            children:
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu0
                config:
                  load_ohms: {load_ohms}
        """)


@pytest.fixture
def sim_yaml(tmp_path):
    p = tmp_path / "sim.yaml"
    p.write_text(_psu_yaml(10), encoding="utf-8")
    return p


# ---- DoD: set 5 V with a 10 ohm load -> measure_current = 0.5 A -----------------------

def test_set_voltage_then_measure_current_follows_ohms_law(sim_yaml):
    with shal.approver(shal.AutoApprove()):
        with shal.load(sim_yaml) as hal:
            dev = hal.get_device("psu")
            dev.set_voltage(5)
            assert dev.measure_voltage() == 5.0
            assert dev.measure_current() == pytest.approx(0.5)


def test_a_second_load_value_gives_a_different_current(tmp_path):
    p = tmp_path / "sim.yaml"
    p.write_text(_psu_yaml(5), encoding="utf-8")
    with shal.approver(shal.AutoApprove()):
        with shal.load(p) as hal:
            dev = hal.get_device("psu")
            dev.set_voltage(5)
            assert dev.measure_current() == pytest.approx(1.0)   # 5V / 5ohm != 0.5A


def test_measure_voltage_before_any_set_is_zero(sim_yaml):
    with shal.load(sim_yaml) as hal:
        dev = hal.get_device("psu")
        assert dev.measure_voltage() == 0.0
        assert dev.measure_current() == 0.0


# ---- config: load_ohms must be a positive number --------------------------------------

@pytest.mark.parametrize("bad", [0, -5, "ten", True])
def test_a_non_positive_or_non_numeric_load_ohms_fails_the_load(tmp_path, bad):
    p = tmp_path / "sim.yaml"
    p.write_text(_psu_yaml(bad), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="load_ohms must be a positive number"):
        shal.load(p)


def test_load_ohms_defaults_when_omitted(tmp_path):
    p = tmp_path / "sim.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          rack:
            driver: shal,sim-scpi
            address: sim0
            children:
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu0
        """), encoding="utf-8")
    with shal.approver(shal.AutoApprove()):
        with shal.load(p) as hal:
            dev = hal.get_device("psu")
            dev.set_voltage(5)
            assert dev.measure_current() == pytest.approx(0.5)   # default 10 ohm


# ---- the write: actuator, gated ---------------------------------------------------------

def test_set_voltage_is_labelled_actuator():
    entry = shal.catalog("shal,sim-psu")
    op = next(o for o in entry["ops"] if o["name"] == "set_voltage")
    assert op["annotations"]["destructiveHint"] is True     # actuator == gated
    fn = sim_scpi_mod.SimPsu.set_voltage
    assert fn.__shal_op__["side_effect"] == "actuator"


def test_measure_ops_are_labelled_none():
    entry = shal.catalog("shal,sim-psu")
    for name in ("measure_voltage", "measure_current"):
        op = next(o for o in entry["ops"] if o["name"] == name)
        assert op["annotations"]["readOnlyHint"] is True


def test_set_voltage_without_approval_writes_nothing_and_says_how(sim_yaml):
    import io
    with shal.approver(shal.ConsoleApprover(stream=io.StringIO())):
        with shal.load(sim_yaml) as hal:
            model = hal.get_node("rack").driver.model_for("psu0")
            with pytest.raises(shal.ApprovalDenied) as err:
                hal.get_device("psu").set_voltage(5)
            assert model.set_count == 0      # nothing reached the device
            assert model.voltage == 0.0
    msg = str(err.value)
    assert "nothing was sent" in msg
    assert "shal.approver(" in msg and "shal mcp" in msg   # how to approve


def test_set_voltage_with_approval_moves_the_output(sim_yaml):
    with shal.approver(shal.AutoApprove()):
        with shal.load(sim_yaml) as hal:
            hal.get_device("psu").set_voltage(12)
            model = hal.get_node("rack").driver.model_for("psu0")
            assert model.set_count == 1
            assert model.voltage == 12.0


def test_set_voltage_is_bounded_before_io(sim_yaml):
    with shal.load(sim_yaml) as hal:
        model = hal.get_node("rack").driver.model_for("psu0")
        with pytest.raises(shal.LimitError):
            hal.get_device("psu").set_voltage(500)
        assert model.set_count == 0


# ---- it wraps no part: only a shal,sim-scpi bus may carry it ---------------------------

def test_refuses_a_real_scpi_bus(tmp_path):
    p = tmp_path / "real.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          host:
            driver: shal,local
            address: localhost
            children:
              rack:
                driver: shal,scpi-raw
                address: 127.0.0.1:5025
                insecure: true
                children:
                  psu0: {id: psu, driver: "shal,sim-psu", address: psu0}
        """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="under a shal,sim-scpi bus"):
        shal.load(p)


# ---- the kit's own check -----------------------------------------------------------

def test_check_driver_reports_zero_problems_and_zero_warnings(sim_yaml):
    report = check_driver("shal,sim-psu", sim_yaml)
    assert report.problems == []
    assert report.warnings == []
    assert any("freshness enforced" in c for c in report.checked)   # D12
    assert any("audit trail present (set_voltage)" in c for c in report.checked)


# ---- D1/D23: what catalog() lists as a driver is the framework's own -------------------

def test_catalog_lists_the_sim_psu_as_a_driver():
    entry = next(d for d in shal.catalog()["drivers"]
                 if d["compatible"] == "shal,sim-psu")
    assert entry["requires_parent_kind"] == "MessageTransport"


# ---- the CLI a cold agent runs first ---------------------------------------------------

def test_tools_lists_set_voltage_as_gated(sim_yaml, capsys):
    assert cli.main(["tools", str(sim_yaml)]) == 0
    out = capsys.readouterr().out
    assert "psu__set_voltage" in out and "[gated]" in out
    assert "psu__measure_voltage" in out and "[read" in out


def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    """Run the real command in a fresh process (not an import of `main`)."""
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "sim.yaml").write_text(_psu_yaml(10), encoding="utf-8")
    return tmp_path


# ---- DoD: agent path — set_voltage is refused, exit 2, and the error names the
#           approve step, non-interactively (no TTY, no --approve flag) --------------

def test_set_voltage_over_cli_is_refused_exit_2_and_names_the_approve_step(lab):
    r = _shal("call", "sim.yaml", "psu", "set_voltage", "5", "--json", cwd=lab)
    assert r.returncode == 2, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["rejected"] == "approval"
    assert out["side_effect"] == "actuator" and out["sent"] is False
    assert any("shal mcp" in w for w in out["approve_with"])
    assert any("shal.approver" in w for w in out["approve_with"])


def test_measure_voltage_over_cli_runs_and_reports_none(lab):
    r = _shal("call", "sim.yaml", "psu", "measure_voltage", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["side_effect"] == "none"
    assert out["result"] == 0.0


def test_check_over_cli_exits_0(lab):
    r = _shal("check", "shal,sim-psu", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["ok"] is True and report["problems"] == []
