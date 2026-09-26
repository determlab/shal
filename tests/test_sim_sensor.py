"""shal,sim-sensor — the sim family's own device (R10, #156).

It ships so a bare `pip install pyshal` has a device to read: a temperature that
drifts (two reads differ; the live value is the model's state) and one gated
`config` op, `set_target`. The catalog tests enforce D1's line on the catalog:
the framework's own objects are `shal,*`, and no `vendor,part` ships.
"""
import functools
import io
import json
import random
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import shal
from shal import cli
from shal.buses import sim as sim_mod
from shal.conformance import check_driver

# the mux chip is a vendor part: it lives in examples/ with its sim model (#149)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples/drivers/pca9548"))
import pca9548_driver  # noqa: E402,F401  (registers nxp,pca9548)
import pca9548_sim  # noqa: E402,F401     (registers its sim model)

_SIM_YAML = textwrap.dedent("""\
    shal_version: 1
    root:
      bus:
        id: bus
        driver: shal,sim-i2c
        address: sim0
        children:
          temp0:
            id: ambient_temp
            driver: shal,sim-sensor
            address: 0x48
    """)


@pytest.fixture
def sim_yaml(tmp_path):
    p = tmp_path / "sim.yaml"
    p.write_text(_SIM_YAML, encoding="utf-8")
    return p


# ---- the read: it drifts, and it is the model's state ------------------------------

def test_two_reads_differ(sim_yaml):
    with shal.load(sim_yaml) as hal:
        dev = hal.get_device("ambient_temp")
        first, second = dev.read_celsius(), dev.read_celsius()
    assert isinstance(first, float) and isinstance(second, float)
    assert first != second


def test_many_reads_all_differ_from_the_one_before(sim_yaml):
    with shal.load(sim_yaml) as hal:
        dev = hal.get_device("ambient_temp")
        reads = [dev.read_celsius() for _ in range(200)]
    assert all(a != b for a, b in zip(reads, reads[1:], strict=False))
    assert all(0 < r < 50 for r in reads)   # drifts around the 25 C target, not away


def test_the_read_is_the_models_state(sim_yaml):
    # rule 3: a sim's live value is its model's state — nothing seeded or cached
    with shal.load(sim_yaml) as hal:
        value = hal.get_device("ambient_temp").read_celsius()
        model = hal.get_node("bus").driver.model_for(0x48)
        assert value == round(model.temp_c, 2)


def test_it_is_a_temperature_sensor(sim_yaml):
    with shal.load(sim_yaml) as hal:
        assert isinstance(hal.get_device("ambient_temp"), shal.TemperatureSensor)


# ---- the write: config, gated ---------------------------------------------------------

def test_set_target_is_labelled_config():
    entry = shal.catalog("shal,sim-sensor")
    op = next(o for o in entry["ops"] if o["name"] == "set_target")
    assert op["annotations"]["destructiveHint"] is True     # config == gated
    fn = sim_mod.SimSensor.set_target
    assert fn.__shal_op__["side_effect"] == "config"


def test_set_target_without_approval_writes_nothing_and_says_how(sim_yaml):
    # the shipped default approver, headless (no TTY): nobody approved
    with shal.approver(shal.ConsoleApprover(stream=io.StringIO())):
        with shal.load(sim_yaml) as hal:
            model = hal.get_node("bus").driver.model_for(0x48)
            with pytest.raises(shal.ApprovalDenied) as err:
                hal.get_device("ambient_temp").set_target(30)
            assert model.target_writes == 0      # nothing reached the device
            assert model.target_c == 25.0
    msg = str(err.value)
    assert "nothing was sent" in msg
    assert "shal.approver(" in msg and "shal mcp" in msg   # how to approve


def test_set_target_with_approval_moves_the_target(sim_yaml):
    with shal.approver(shal.AutoApprove()):
        with shal.load(sim_yaml) as hal:
            hal.get_device("ambient_temp").set_target(30)
            model = hal.get_node("bus").driver.model_for(0x48)
            assert model.target_writes == 1
            assert model.target_c == 30.0


def test_set_target_is_bounded_before_io(sim_yaml):
    with shal.load(sim_yaml) as hal:
        model = hal.get_node("bus").driver.model_for(0x48)
        with pytest.raises(shal.LimitError):
            hal.get_device("ambient_temp").set_target(500)
        assert model.target_writes == 0


# ---- it wraps no part: only a sim bus may carry it ------------------------------------

def test_behind_a_mux_on_the_sim_bus_still_binds(tmp_path):
    p = tmp_path / "mux.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          bus:
            driver: shal,sim-i2c
            address: sim0
            children:
              mux:
                driver: nxp,pca9548
                address: 0x70
                children:
                  ch0:
                    address: 0
                    children:
                      t: {id: t, driver: "shal,sim-sensor", address: 0x48}
        """), encoding="utf-8")
    with shal.load(p) as hal:
        dev = hal.get_device("t")
        assert dev.read_celsius() != dev.read_celsius()


def test_refuses_a_real_bus(tmp_path):
    p = tmp_path / "real.yaml"
    p.write_text(textwrap.dedent("""\
        shal_version: 1
        root:
          host:
            driver: shal,local
            address: localhost
            children:
              i2c0:
                driver: shal,i2c-cli
                address: /dev/i2c-1
                children:
                  t: {id: t, driver: "shal,sim-sensor", address: 0x48}
        """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="under a shal,sim-i2c bus"):
        shal.load(p)


# ---- the CLI a cold agent runs first ---------------------------------------------------

def test_probe_two_runs_print_two_values(sim_yaml, capsys, monkeypatch):
    # each run is a fresh model with a random start; pin two different seeds so
    # the test cannot flake on a 1-in-400 equal start
    seeds = iter([1, 2])
    real_init = sim_mod.SimSensorModel.__init__

    def seeded(self, rng=None):
        real_init(self, random.Random(next(seeds)))
    monkeypatch.setattr(sim_mod.SimSensorModel, "__init__", seeded)
    values = []
    for _ in range(2):
        assert cli.main(["probe", str(sim_yaml)]) == 0
        out = capsys.readouterr().out
        m = re.search(r"^ambient_temp__read_celsius: (-?\d+\.\d+)$", out, re.M)
        assert m, out
        values.append(float(m.group(1)))
    assert values[0] != values[1]


def test_tools_lists_set_target_as_gated(sim_yaml, capsys):
    assert cli.main(["tools", str(sim_yaml)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"ambient_temp__set_target\s+\[gated\]", out), out
    assert re.search(r"ambient_temp__read_celsius\s+\[read", out), out


# ---- the kit's own check -----------------------------------------------------------

def test_check_driver_reports_zero_problems_and_zero_warnings(sim_yaml):
    report = check_driver("shal,sim-sensor", sim_yaml)
    assert report.problems == []
    assert report.warnings == []
    assert any("freshness enforced" in c for c in report.checked)   # D12
    assert any("audit trail present (set_target)" in c for c in report.checked)


# ---- D1, enforced: what catalog() lists as a driver is the framework's own ------------

def test_catalog_lists_the_sim_sensor_as_a_driver():
    entry = next(d for d in shal.catalog()["drivers"]
                 if d["compatible"] == "shal,sim-sensor")
    assert entry["capability"] == "TemperatureSensor"
    assert entry["requires_parent_kind"] == "ByteTransport"


@functools.lru_cache(maxsize=1)
def _catalog_ids() -> tuple[str, ...]:
    # what SHIPS: a fresh interpreter, so drivers other tests register in this
    # process do not count. D1's line covers every framework object, buses too.
    code = ("import json, shal; c = shal.catalog(); "
            "print(json.dumps([e['compatible'] for e in c['drivers'] + c['buses']]))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout
    return tuple(json.loads(out))


def test_every_catalog_id_is_a_shal_compatible():
    offenders = [c for c in _catalog_ids() if not c.startswith("shal,")]
    assert offenders == [], (
        f"D1: only shal,* ships; vendor,part ids in catalog(): {offenders}")


# ---- ApprovalDenied survives a pickle round-trip without doubling its hint ------------

def test_approval_denied_pickle_round_trip_keeps_one_hint():
    import pickle
    err = shal.ApprovalDenied("/bus/temp0  set_target denied", op="set_target")
    back = pickle.loads(pickle.dumps(err))
    assert str(back) == str(err)
    assert str(back).count("to approve:") == 1
