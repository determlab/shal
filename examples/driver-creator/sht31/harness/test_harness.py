"""Validation harness for a GENERATED sensirion,sht31 driver (issue #10).

Runs the generated driver against the benchmark's OWN behavioral sim model
(sim_harness.py, written independently from the same datasheet), never against
the generator's sim. Vectors come straight from the datasheet's worked
examples (docs/sht31-datasheet.md section 6).

The SHT31 is read-only — there are no settable quantities, hence no
LimitError tests; the conformance report standing green is the gate instead.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import shal
from shal import conformance
from shal.buses import sim as sim_bus

HERE = Path(__file__).resolve().parent
GEN = HERE.parent / "generated"
TOPOLOGY = str(HERE / "topology.yaml")

pytestmark = pytest.mark.skipif(not (GEN / "driver.py").exists(),
                                reason="not generated yet")

if (GEN / "driver.py").exists():
    sys.path.insert(0, str(GEN))
    import driver  # noqa: F401  -- registers the generated driver class

    sys.path.insert(0, str(HERE))
    import sim_harness

    # Force the harness model to win: whatever model the generator registered
    # for this compatible is overwritten so validation runs against OUR sim.
    sim_bus.SIM_MODELS["sensirion,sht31"] = sim_harness.Sht31HarnessModel

# Datasheet worked examples (section 6) — exact IEEE-754 doubles.
T_0x6666 = 25.0                    # raw 26214
T_0x851E = 45.99946593423361       # raw 34078
RH_0x8000 = 50.000762951094835     # raw 32768  (model state 50.0 encodes to it)
RH_0x3333 = 20.0                   # raw 13107


def _model(hal):
    return hal.get_node("bench").driver.model_for(0x44)


def test_capabilities():
    with shal.load(TOPOLOGY) as hal:
        dev = hal.get_device("dut")
        assert isinstance(dev, shal.TemperatureSensor)
        assert callable(getattr(dev, "read_humidity_percent", None)), \
            "driver must expose read_humidity_percent (humidity is a first-class op)"


def test_temperature_worked_example_0x6666():
    with shal.load(TOPOLOGY) as hal:
        # model default temp_c = 25.0 encodes to raw 0x6666 exactly
        assert hal.get_device("dut").read_celsius() == pytest.approx(
            T_0x6666, abs=0.01)


def test_temperature_worked_example_0x851e():
    with shal.load(TOPOLOGY) as hal:
        _model(hal).temp_c = T_0x851E      # encodes to raw 0x851E exactly
        assert hal.get_device("dut").read_celsius() == pytest.approx(
            T_0x851E, abs=0.01)


def test_humidity_worked_example_0x8000():
    with shal.load(TOPOLOGY) as hal:
        # model default rh_percent = 50.0 encodes to raw 0x8000; decoding that
        # raw gives the datasheet's 50.000762951094835 %RH
        assert hal.get_device("dut").read_humidity_percent() == pytest.approx(
            RH_0x8000, abs=0.01)


def test_humidity_worked_example_0x3333():
    with shal.load(TOPOLOGY) as hal:
        _model(hal).rh_percent = RH_0x3333  # encodes to raw 0x3333 exactly
        assert hal.get_device("dut").read_humidity_percent() == pytest.approx(
            RH_0x3333, abs=0.01)


def test_conformance_report_ok():
    # Read-only device: no declared limits to probe, so the conformance kit
    # (llm_ready, @op metadata, catalog schemas, capability isinstance, audit)
    # is the acceptance gate.
    report = conformance.check_driver("sensirion,sht31", topology=TOPOLOGY)
    assert report.ok, str(report)
