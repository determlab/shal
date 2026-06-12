"""Independent validation harness for the driver-creator benchmark, case 3
(HTTP service from an OpenAPI spec — Lumen ChamberLink, "lumen,chamber-api").

Validates a GENERATED driver (../generated/driver.py) against the harness's
OWN sim model (sim_harness.py), which was written independently from
../docs/. After importing both the generated artifacts and the harness model,
we force the harness model to win the sim-bus registry slot, so every
assertion below runs against OUR behavioral oracle — not the sim the
generation agent wrote for itself.

Run from the repo root:
    python -m pytest examples/driver-creator/http-service/harness -q
Skips cleanly while generated/ does not exist yet.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import shal
from shal import conformance

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "generated"
TOPO = HERE / "topology.yaml"
COMPAT = "lumen,chamber-api"

pytestmark = pytest.mark.skipif(not (GEN / "driver.py").exists(),
                                reason="not generated yet")

if (GEN / "driver.py").exists():
    sys.path.insert(0, str(HERE))
    sys.path.insert(0, str(GEN))          # GEN first: its driver/sim win imports
    import driver                          # noqa: F401  registers the generated driver
    try:
        import sim as _generated_sim       # noqa: F401  their sim may self-register…
    except ImportError:
        pass
    import sim_harness                     # …but OUR model must win:
    from shal.buses import sim_msg
    sim_msg.MSG_SIM_MODELS[COMPAT] = sim_harness.ChamberLinkModel


@pytest.fixture()
def hal():
    with shal.load(str(TOPO)) as h:
        yield h


@pytest.fixture()
def dev(hal):
    return hal.get_device("dut")


@pytest.fixture()
def model(hal, dev):
    m = hal.get_node("bench").driver.model_for("chamber")
    assert isinstance(m, sim_harness.ChamberLinkModel), \
        "harness model did not win the sim registry — validation would be circular"
    return m


# ---- worked-example vectors (docs/chamberlink-notes.md table, OpenAPI examples) ----

def test_capability_protocol(dev):
    assert isinstance(dev, shal.TemperatureSensor)


def test_vector_1_get_status_soaking_at_65(dev, model):
    model.setpoint_c = 65.0
    model.temp_c = 65.0
    model.running = True
    model.door_open = False
    assert dev.read_celsius() == pytest.approx(65.0)
    status = dev.read_status()
    assert status["temp_c"] == pytest.approx(65.0)
    assert status["setpoint_c"] == pytest.approx(65.0)
    assert status["door_open"] is False
    assert status["running"] is True


def test_vector_2_set_temperature_85_5(dev, model):
    dev.set_temperature(85.5)
    assert model.setpoint_c == pytest.approx(85.5)
    # per the docs: a subsequent get_status reports the new setpoint, and the
    # settled chamber converges to it
    assert dev.read_celsius() == pytest.approx(85.5)
    assert dev.read_status()["setpoint_c"] == pytest.approx(85.5)


def test_vector_3_start(dev, model):
    assert model.running is False
    dev.start()
    assert model.running is True
    dev.start()                            # level-setting: harmless repeat
    assert model.running is True


def test_vector_4_stop(dev, model):
    model.running = True
    dev.stop()
    assert model.running is False


def test_envelope_boundaries_accepted(dev, model):
    dev.set_temperature(180.0)
    assert model.setpoint_c == pytest.approx(180.0)
    dev.set_temperature(-40.0)
    assert model.setpoint_c == pytest.approx(-40.0)


# ---- declared limits must reject pre-I/O (vector 5 + the low bound) ----------------

def test_limit_rejects_celsius_200(dev, model):
    before = model.setpoint_c
    with pytest.raises(shal.LimitError):
        dev.set_temperature(200.0)
    assert model.setpoint_c == before, "out-of-range setpoint reached the device"


def test_limit_rejects_celsius_minus_50(dev, model):
    before = model.setpoint_c
    with pytest.raises(shal.LimitError):
        dev.set_temperature(-50.0)
    assert model.setpoint_c == before, "out-of-range setpoint reached the device"


# ---- conformance: the definition of done -------------------------------------------

def test_conformance_report_ok():
    report = conformance.check_driver(COMPAT, topology=str(TOPO))
    assert report.ok, str(report)
