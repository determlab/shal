"""shal,sim-dmm — the sim family's third device (#303).

It ships so a DMM reading has something to measure with no hardware:
`config.probe` names a `shal,sim-psu` address on the same `shal,sim-scpi` bus,
and `measure_voltage`/`measure_current` read that PSU's own measurement (so
`load_ohms` and constant-current mode already apply) plus small seeded noise.
"""
import json
import textwrap

import pytest

import shal
from shal.conformance import check_driver


def _bench_yaml(load_ohms: float = 10) -> str:
    return textwrap.dedent(f"""\
        shal_version: 1
        root:
          bench:
            id: bench
            driver: shal,sim-scpi
            address: sim0
            children:
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu0
                config:
                  load_ohms: {load_ohms}
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
                config:
                  probe: psu0
        """)


@pytest.fixture
def sim_yaml(tmp_path):
    p = tmp_path / "bench.yaml"
    p.write_text(_bench_yaml(), encoding="utf-8")
    return p


# ---- DoD: PSU at 3.3 V -> MEAS:VOLT:DC? within 1% of 3.3 ------------------------------

def test_measure_voltage_is_within_one_percent_of_the_psu_setting(sim_yaml):
    with shal.approver(shal.AutoApprove()):
        with shal.load(sim_yaml) as hal:
            hal.get_device("psu").set_voltage(3.3)
            volts = hal.get_device("dmm").measure_voltage()
            assert volts == pytest.approx(3.3, rel=0.01)


# ---- DoD: MEAS:CURR:DC? follows the load ----------------------------------------------

def test_measure_current_follows_the_psu_load(tmp_path):
    p = tmp_path / "bench.yaml"
    p.write_text(_bench_yaml(load_ohms=5), encoding="utf-8")
    with shal.approver(shal.AutoApprove()):
        with shal.load(p) as hal:
            hal.get_device("psu").set_voltage(5)
            amps = hal.get_device("dmm").measure_current()
            assert amps == pytest.approx(1.0, rel=0.01)   # 5V / 5ohm


def test_measure_current_tracks_a_different_load(sim_yaml):
    with shal.approver(shal.AutoApprove()):
        with shal.load(sim_yaml) as hal:
            hal.get_device("psu").set_voltage(5)
            amps = hal.get_device("dmm").measure_current()
            assert amps == pytest.approx(0.5, rel=0.01)   # 5V / 10ohm


def test_measure_current_respects_constant_current_mode(tmp_path):
    p = tmp_path / "bench.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu0
                config:
                  load_ohms: 10
                  current_limit: 0.3
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
                config:
                  probe: psu0
        """), encoding="utf-8")
    with shal.approver(shal.AutoApprove()):
        with shal.load(p) as hal:
            hal.get_device("psu").set_voltage(5)   # 0.5A uncapped, but limited to 0.3A
            assert hal.get_device("dmm").measure_current() == pytest.approx(0.3, rel=0.01)
            assert hal.get_device("dmm").measure_voltage() == pytest.approx(3.0, rel=0.01)


# ---- DoD: the same seed gives the same value -------------------------------------------

def test_same_seed_gives_the_same_reading(sim_yaml):
    readings = []
    for _ in range(2):
        with shal.approver(shal.AutoApprove()):
            with shal.load(sim_yaml) as hal:
                hal.get_device("psu").set_voltage(3.3)
                readings.append(hal.get_device("dmm").measure_voltage())
    assert readings[0] == readings[1]


def test_a_different_probe_address_seeds_a_different_reading(tmp_path):
    p1 = tmp_path / "a.yaml"
    p1.write_text(_bench_yaml(), encoding="utf-8")
    p2 = tmp_path / "b.yaml"
    p2.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              psu0:
                id: psu
                driver: shal,sim-psu
                address: psu1
                config:
                  load_ohms: 10
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
                config:
                  probe: psu1
        """), encoding="utf-8")
    with shal.approver(shal.AutoApprove()):
        with shal.load(p1) as hal:
            hal.get_device("psu").set_voltage(3.3)
            a = hal.get_device("dmm").measure_voltage()
        with shal.load(p2) as hal:
            hal.get_device("psu").set_voltage(3.3)
            b = hal.get_device("dmm").measure_voltage()
    assert a != b


# ---- DoD: a sim-dmm outside a sim-scpi bus gives a LoadError --------------------------

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
                  dmm0: {id: dmm, driver: "shal,sim-dmm", address: dmm0,
                        config: {probe: psu0}}
        """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="under a shal,sim-scpi bus"):
        shal.load(p)


# ---- config: probe must name a real address --------------------------------------------

@pytest.mark.parametrize("bad", ["", 5, True])
def test_a_bad_probe_fails_the_load(tmp_path, bad):
    p = tmp_path / "bench.yaml"
    p.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
                config:
                  probe: {bad!r}
        """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="config.probe must name"):
        shal.load(p)


def test_a_missing_probe_fails_the_load(tmp_path):
    p = tmp_path / "bench.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              dmm0:
                id: dmm
                driver: shal,sim-dmm
                address: dmm0
        """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="config.probe must name"):
        shal.load(p)


def test_a_probe_address_that_does_not_exist_fails_at_read_time(sim_yaml, tmp_path):
    p = tmp_path / "bench.yaml"
    p.write_text(_bench_yaml().replace("probe: psu0", "probe: nope"), encoding="utf-8")
    with shal.load(p) as hal:
        with pytest.raises(LookupError, match="nope"):
            hal.get_device("dmm").measure_voltage()


# ---- the ops: reads, never gated --------------------------------------------------------

def test_measure_ops_are_labelled_none():
    entry = shal.catalog("shal,sim-dmm")
    for name in ("measure_voltage", "measure_current"):
        op = next(o for o in entry["ops"] if o["name"] == name)
        assert op["annotations"]["readOnlyHint"] is True


# ---- the kit's own check -----------------------------------------------------------

def test_check_driver_reports_zero_problems_and_zero_warnings(sim_yaml):
    report = check_driver("shal,sim-dmm", sim_yaml)
    assert report.problems == []
    assert report.warnings == []


# ---- D1/D23: what catalog() lists as a driver is the framework's own -------------------

def test_catalog_lists_the_sim_dmm_as_a_driver():
    entry = next(d for d in shal.catalog()["drivers"]
                 if d["compatible"] == "shal,sim-dmm")
    assert entry["requires_parent_kind"] == "MessageTransport"


# ---- the agent path: the CLI a cold agent runs first -----------------------------------

def test_tools_lists_measure_ops_as_read(sim_yaml, capsys):
    from shal import cli
    assert cli.main(["tools", str(sim_yaml)]) == 0
    out = capsys.readouterr().out
    assert "dmm__measure_voltage" in out and "[read" in out
    assert "dmm__measure_current" in out and "[read" in out


def test_measure_voltage_over_cli_runs_and_reports_the_default(tmp_path):
    import subprocess
    import sys
    (tmp_path / "bench.yaml").write_text(_bench_yaml(), encoding="utf-8")
    # set_voltage is gated (actuator) and never run non-interactively here, so
    # the PSU's output is still at its unset default: 0 V -> 0 V measured.
    r = subprocess.run([sys.executable, "-m", "shal.cli", "call", "bench.yaml", "dmm",
                       "measure_voltage", "--json"], cwd=tmp_path,
                      capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["side_effect"] == "none"
    assert out["result"] == 0.0
