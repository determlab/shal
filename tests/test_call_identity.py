"""Every call result/log line carries the instrument it came from (#347):
``device`` (the node id), ``address`` (redacted when it is a string) and
``simulated`` (the driver's own ``simulated`` class attribute, never inferred
from the id). Covers ``Hal.call_tool``'s result and the op wrapper's own
``shal`` log line (``driver.py``, the single instrumentation point)."""
import logging
from pathlib import Path

import pytest

import shal

_BENCH = (Path(__file__).resolve().parents[1] / "examples" / "demos"
         / "virtual-bench" / "bench.yaml")


def _call_lines(caplog) -> list:
    return [r for r in caplog.records if getattr(r, "event", "") == "call"]


# ---- DoD 1: a DMM measurement on the shipped virtual bench -----------------------

def test_dmm_measurement_result_and_call_line_carry_identity(caplog):
    with shal.load(_BENCH) as hal:
        with caplog.at_level(logging.DEBUG, logger="shal"):
            out = hal.call_tool("dmm__measure_voltage")
    assert out["ok"] is True
    assert out["device"] == "dmm"
    assert out["address"] == "dmm0"
    assert out["simulated"] is True
    [call] = [r for r in _call_lines(caplog) if r.device == "dmm"]
    assert call.address == "dmm0" and call.simulated is True


def test_psu_write_result_and_audit_line_carry_identity(caplog):
    """A gated/audited op (`side_effect="actuator"`) too — on the `shal.audit`
    channel, not only the DEBUG call line."""
    records: list = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    audit = logging.getLogger("shal.audit")
    handler = Collect(level=logging.INFO)
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        with shal.load(_BENCH, approver=shal.AutoApprove()) as hal:
            out = hal.call_tool("psu__set_voltage", {"volts": 3.3})
    finally:
        audit.removeHandler(handler)
        audit.setLevel(logging.NOTSET)
    assert out["ok"] is True
    assert out["device"] == "psu" and out["address"] == "psu0" and out["simulated"] is True
    [ok] = [r for r in records if getattr(r, "outcome", "") == "ok"]
    assert ok.device == "psu" and ok.address == "psu0" and ok.simulated is True


# ---- DoD 2: a mixed bench — each driver's own `simulated` value ------------------

@shal.register
class _CiRealProbe(shal.Driver):
    """Stands in for a real instrument: `simulated` stays the default, False."""

    compatible = "test,ci-real"
    kind = None  # root node: no parent bus needed

    @shal.op("Read the real probe.", side_effect="none")
    def read(self) -> int:
        return 1


@shal.register
class _CiSimProbe(shal.Driver):
    compatible = "test,ci-sim"
    kind = None
    simulated = True

    @shal.op("Read the sim probe.", side_effect="none")
    def read(self) -> int:
        return 2


_MIXED_YAML = """\
shal_version: 1
root:
  real:
    id: real
    driver: "test,ci-real"
    address: "real-0"
  sim:
    id: sim
    driver: "test,ci-sim"
    address: "sim-0"
"""


@pytest.fixture
def mixed_hal(tmp_path):
    p = tmp_path / "mixed.yaml"
    p.write_text(_MIXED_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal


def test_mixed_bench_each_result_carries_its_own_drivers_simulated_value(mixed_hal, caplog):
    with caplog.at_level(logging.DEBUG, logger="shal"):
        real_out = mixed_hal.call_tool("real__read")
        sim_out = mixed_hal.call_tool("sim__read")
    assert real_out["device"] == "real" and real_out["address"] == "real-0"
    assert real_out["simulated"] is False
    assert sim_out["device"] == "sim" and sim_out["address"] == "sim-0"
    assert sim_out["simulated"] is True
    by_device = {c.device: c for c in _call_lines(caplog)}
    assert by_device["real"].simulated is False
    assert by_device["sim"].simulated is True


# ---- DoD 3: a string address is redacted (#20) ------------------------------------

@shal.register
class _CiUrlProbe(shal.Driver):
    compatible = "test,ci-url"
    kind = None

    @shal.op("Read.", side_effect="none")
    def read(self) -> int:
        return 3


_URL_YAML = """\
shal_version: 1
root:
  probe:
    id: probe
    driver: "test,ci-url"
    address: "https://user:topsecret@host.example:8443/path?token=abc123"
"""


def test_address_is_redacted_through_redact_url(tmp_path):
    p = tmp_path / "url.yaml"
    p.write_text(_URL_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        out = hal.call_tool("probe__read")
    assert out["ok"] is True
    assert out["address"] == "https://host.example:8443/path"
    assert "topsecret" not in out["address"]
    assert "abc123" not in out["address"]


# ---- an int address is reported as-is, and `simulated` is the DRIVER's, never
# inferred from the id: `setup_sim.yaml`'s `ambient_temp` runs the real-hardware
# `ti,tmp102` driver against a simulated bus — the driver is not a sim, so
# `simulated` is False even though the bus underneath it is ------------------------

def test_int_address_is_unchanged_and_simulated_follows_the_driver():
    with shal.load(Path(__file__).parent / "setup_sim.yaml") as hal:
        out = hal.call_tool("ambient_temp__read_celsius")
    assert out["device"] == "ambient_temp"
    assert out["address"] == 0x48
    assert out["simulated"] is False
