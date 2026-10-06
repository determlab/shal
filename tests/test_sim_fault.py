"""`fault: unplugged` / `SHAL_SIM_UNPLUG` — sim fault injection (issue #304).

A device node with `fault: unplugged`, or named by the `SHAL_SIM_UNPLUG` env var,
refuses every hop exactly like a real disconnected link: `HopError(delivered="no")`
with the instrument's address (host:port style, as a real tcp-backed instrument
would have) in the text. A node without either behaves exactly as today, and the
CLI reports the failure as `Unreachable` (shal#300) — same type and exit code as
any other link that never delivered.
"""
import json
import subprocess
import sys
import textwrap

import pytest

import shal
from shal.errors import HopError


def write(tmp_path, body: str):
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


_TOPO = """
    shal_version: 1
    root:
      bench:
        id: bench
        driver: shal,sim-scpi
        address: sim0
        children:
          psu0:
            id: psu0
            driver: shal,sim-psu
            address: {psu0_addr}
            {psu0_fault}
          psu1:
            id: psu1
            driver: shal,sim-psu
            address: psu1
"""


def _topo(tmp_path, psu0_addr="192.0.2.5:5025", faulted=True):
    fault = "fault: unplugged" if faulted else ""
    return write(tmp_path, _TOPO.format(psu0_addr=psu0_addr, psu0_fault=fault))


# ---- `fault: unplugged` in the topology file ---------------------------------------

def test_fault_unplugged_raises_hop_error_delivered_no_with_host_port(tmp_path):
    p = _topo(tmp_path)
    with shal.load(p) as hal:
        with pytest.raises(HopError) as ei:
            hal.get_device("psu0").measure_voltage()
        assert ei.value.delivered == "no"
        assert "192.0.2.5:5025" in str(ei.value)


def test_fault_unplugged_other_device_on_same_bus_still_answers(tmp_path):
    p = _topo(tmp_path)
    with shal.load(p) as hal:
        with pytest.raises(HopError):
            hal.get_device("psu0").measure_voltage()
        # psu1, same bus, no fault: unaffected
        assert hal.get_device("psu1").measure_voltage() == pytest.approx(0.0)
        hal.get_device("psu1").set_voltage(3.3)
        assert hal.get_device("psu1").measure_voltage() == pytest.approx(3.3)


def test_no_fault_and_no_env_var_behaves_as_today(tmp_path):
    p = _topo(tmp_path, faulted=False)
    with shal.load(p) as hal:
        assert hal.get_device("psu0").measure_voltage() == pytest.approx(0.0)


# ---- `SHAL_SIM_UNPLUG=<node id>` does the same without editing the file ------------

def test_shal_sim_unplug_env_var_raises_hop_error_delivered_no_with_host_port(
        tmp_path, monkeypatch):
    p = _topo(tmp_path, faulted=False)
    monkeypatch.setenv("SHAL_SIM_UNPLUG", "psu0")
    with shal.load(p) as hal:
        with pytest.raises(HopError) as ei:
            hal.get_device("psu0").measure_voltage()
        assert ei.value.delivered == "no"
        assert "192.0.2.5:5025" in str(ei.value)
        # the OTHER device on the same bus, not named by the env var, still answers
        assert hal.get_device("psu1").measure_voltage() == pytest.approx(0.0)


def test_shal_sim_unplug_naming_a_different_real_id_changes_only_that_one(
        tmp_path, monkeypatch):
    p = _topo(tmp_path, faulted=False)
    monkeypatch.setenv("SHAL_SIM_UNPLUG", "psu1")
    with shal.load(p) as hal:
        # psu0, the one this test would otherwise probe, is untouched --
        # only the NAMED id (psu1) is unplugged.
        assert hal.get_device("psu0").measure_voltage() == pytest.approx(0.0)
        with pytest.raises(HopError):
            hal.get_device("psu1").measure_voltage()


def test_shal_sim_unplug_naming_an_unknown_id_raises_load_error(tmp_path, monkeypatch):
    # issue #417: this used to silently unplug nothing -- a false "healthy"
    # pass, since nothing matched `some-other-node` at all. Covered in
    # full detail (the real sample, the error text) by
    # tests/test_sim_unplug_id.py; this is the smoke test on THIS file's
    # own fixture topology.
    p = _topo(tmp_path, faulted=False)
    monkeypatch.setenv("SHAL_SIM_UNPLUG", "some-other-node")
    with pytest.raises(shal.LoadError) as ei:
        shal.load(p)
    assert "some-other-node" in str(ei.value)


# ---- the CLI reports it as `Unreachable`, same type and exit code as shal#300 -----

def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


def test_cli_call_on_an_unplugged_device_is_unreachable_exit_4_with_host_port(tmp_path):
    p = _topo(tmp_path)
    r = _shal("call", p.name, "psu0", "measure_voltage", "--json", cwd=tmp_path)
    assert r.returncode == 4, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is False and out["delivered"] == "no"
    assert out["error"]["type"] == "Unreachable"
    assert "192.0.2.5:5025" in out["error"]["message"]


def test_cli_probe_still_runs_the_other_device_when_one_is_unplugged(tmp_path):
    p = _topo(tmp_path)
    r = _shal("probe", p.name, "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr  # per-read error; the whole probe still runs
    reads = {read["tool"]: read for read in json.loads(r.stdout)["reads"]}
    assert reads["psu0__measure_voltage"]["ok"] is False
    assert reads["psu0__measure_voltage"]["error"]["type"] == "Unreachable"
    assert reads["psu1__measure_voltage"]["ok"] is True
