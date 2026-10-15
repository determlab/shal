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


@pytest.mark.parametrize("bare,expected", [
    ("h:5025?token=s3cret", "h:5025"),
    ("h/api?key=s3cret", "h/api"),
    ("h:5025#frag", "h:5025"),
    ("user:pw@h:5025?token=s3cret", "h:5025"),
])
def test_a_scheme_less_address_drops_its_query_or_fragment_too(bare, expected):
    """#347 round 2 must-fix 3: a secret can ride a scheme-less address as a
    query param (`h:5025?token=s`), not only as URL userinfo -- `redact_url`'s
    bare-address branch used to keep everything after the first `@` as-is."""
    from shal.log import redact_url
    assert redact_url(bare) == expected


# ---- DoD 2' (#347 round 2 must-fix 2): a pinned route reports ITS OWN address,
# not the main route's -- `shal call ... --via jump` answered on 0x49, but the
# old code reported the node's own configured address (0x48) regardless -------

_ROUTES_YAML = """\
shal_version: 1
root:
  console:
    driver: shal,sim-i2c
    address: sim0
    children:
      board:
        id: board
        driver: shal,sim-sensor
        address: 0x48
        routes:
          - {via: /net, address: 0x49, name: ssh}
  net:
    driver: shal,sim-i2c
    address: sim1
    children:
      twin: {driver: "shal,sim-sensor", address: 0x49}
"""


def test_a_pinned_route_reports_the_address_it_actually_answered_on(tmp_path, caplog):
    p = tmp_path / "routes.yaml"
    p.write_text(_ROUTES_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        with caplog.at_level(logging.DEBUG, logger="shal"):
            out = hal.call_tool("board__read_celsius", {"via": "ssh"})
    assert out["ok"] is True and out["via"] == "ssh"
    assert out["address"] == 0x49
    [call] = [r for r in _call_lines(caplog) if r.device == "board"]
    assert call.address == 0x49


# ---- an int address is reported as-is. `simulated` is never inferred from the
# id or `compatible`: it is the driver's own flag OR'd with the flag of the
# transport that carried the call (#347 round 2 must-fix) -- `setup_sim.yaml`'s
# `ambient_temp` runs the real-hardware `ti,tmp102` driver against a simulated
# bus, and the driver itself never declares `simulated`, but the reading is
# still simulated because the bus underneath it is -----------------------------

def test_int_address_is_unchanged_and_simulated_follows_the_driver_or_the_bus():
    with shal.load(Path(__file__).parent / "setup_sim.yaml") as hal:
        out = hal.call_tool("ambient_temp__read_celsius")
    assert out["device"] == "ambient_temp"
    assert out["address"] == 0x48
    assert out["simulated"] is True


def test_every_registered_sim_bus_or_device_declares_simulated_true():
    """#347 round 2 enforcement: every `shal,sim-*` class SHAL ships (the three
    transports a device sits on, and the device models that ship with them)
    declares `simulated = True` on itself -- never relies on inference."""
    from shal.registry import catalog, resolve
    sim_compatibles = [e["compatible"] for e in
                       catalog()["buses"] + catalog()["drivers"]
                       if e["compatible"].startswith("shal,sim-")]
    assert sim_compatibles  # the catalog actually has them, or this test proves nothing
    for compatible in sim_compatibles:
        cls = resolve(compatible)
        assert cls.simulated is True, f"{compatible} ({cls.__name__}) must declare simulated = True"
