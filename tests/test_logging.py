"""Logging & observability suite (DESIGN V2 'Logging'): retry WARNING, raise
breadcrumbs, event fields, formatters, capture() flight recorder, audit channel.
"""
import json
import logging
from pathlib import Path

import pytest

import shal

SETUP = Path(__file__).parent / "setup_sim.yaml"  # the core suite's sim topology


# ---- a tiny write-capable device driver for audit tests --------------------------

@shal.register
class _FakeMotor(shal.Driver):
    compatible = "test,motor"
    kind = None  # root node: no parent bus needed

    def spin(self, rpm: int = 100) -> str:   # write op: NOT idempotent
        return f"spinning {rpm}"

    @shal.idempotent
    def get_rpm(self) -> int:                # read op: idempotent, not audited
        return 100


MOTOR_YAML = """\
shal_version: 1
root:
  m: {id: m, driver: "test,motor", address: 1}
"""


@pytest.fixture
def motor_hal(tmp_path):
    p = tmp_path / "motor.yaml"
    p.write_text(MOTOR_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal


# ---- slice 3: retry WARNING + raise breadcrumbs ----------------------------------

def test_retry_emits_warning(caplog):
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_next = 1
        with caplog.at_level(logging.WARNING, logger="shal"):
            dev.read_celsius()  # retried transparently — but never silently
    [rec] = [r for r in caplog.records if getattr(r, "event", "") == "retry"]
    assert rec.levelno == logging.WARNING
    assert rec.op == "read_celsius"
    assert rec.attempt == 2
    assert rec.txn != "----"  # correlated to the capability call


def test_raise_leaves_debug_breadcrumb(caplog):
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_delivered_unknown = True
        with caplog.at_level(logging.DEBUG, logger="shal"):
            with pytest.raises(shal.HopError):
                dev.read_celsius()
    [rec] = [r for r in caplog.records if getattr(r, "event", "") == "raise"]
    assert rec.levelno == logging.DEBUG  # breadcrumb, never ERROR (raise-or-log)
    assert rec.delivered == "unknown"


def test_no_error_records_for_raised_exceptions(caplog):
    """Raise-or-log: an exception surfaced to the caller is never ALSO an ERROR."""
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_next = 2
        with caplog.at_level(logging.DEBUG, logger="shal"):
            with pytest.raises(shal.HopError):
                dev.read_celsius()
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ---- slice 1+4: event fields ride every hop record --------------------------------

def test_hop_records_carry_event_and_txn(caplog):
    with shal.load(SETUP) as hal:
        with caplog.at_level(logging.DEBUG, logger="shal"):
            hal.get_device("ambient_temp").read_celsius()
    events = {getattr(r, "event", None) for r in caplog.records}
    assert {"activate", "txn", "call"} <= events
    txn_recs = [r for r in caplog.records if getattr(r, "event", "") == "txn"]
    call_recs = [r for r in caplog.records if getattr(r, "event", "") == "call"]
    assert txn_recs[0].txn == call_recs[0].txn  # one txn id, whole story


# ---- slice 2: formatters -----------------------------------------------------------

def _one_record(caplog):
    with shal.load(SETUP) as hal:
        with caplog.at_level(logging.DEBUG, logger="shal"):
            hal.get_device("ambient_temp").read_celsius()
    return [r for r in caplog.records if getattr(r, "event", "") == "txn"][0]


def test_console_formatter_renders_fields_tail(caplog):
    rec = _one_record(caplog)
    line = shal.logging.ConsoleFormatter().format(rec)
    assert "event=txn" in line and "txn=" in line and line.startswith("DEBUG")


def test_json_formatter_roundtrips_all_fields(caplog):
    rec = _one_record(caplog)
    obj = json.loads(shal.logging.JSONFormatter().format(rec))
    assert obj["event"] == "txn"
    assert obj["logger"] == "shal.bus.sim_i2c"
    assert {"ts", "level", "msg", "path", "txn", "addr"} <= obj.keys()


# ---- slice 5: capture() flight recorder --------------------------------------------

def test_capture_writes_jsonl_regardless_of_console_level(tmp_path):
    out = tmp_path / "debug.jsonl"
    with shal.load(SETUP) as hal:
        with shal.logging.capture(out):
            hal.get_device("ambient_temp").read_celsius()
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert any(o["event"] == "txn" for o in lines if "event" in o)  # DEBUG captured


def test_capture_appends_escaping_exception(tmp_path):
    out = tmp_path / "debug.jsonl"
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_delivered_unknown = True
        with pytest.raises(shal.HopError):
            with shal.logging.capture(out):
                dev.read_celsius()
    lines = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    [exc] = [o for o in lines if o.get("event") == "exception"]
    assert "HopError" in exc["msg"] and "exc" in exc  # the story doesn't just stop


# ---- slice 6: audit channel ---------------------------------------------------------

@pytest.fixture
def audit_records():
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level=logging.INFO)
    audit = logging.getLogger("shal.audit")
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    yield records
    audit.removeHandler(handler)
    audit.setLevel(logging.NOTSET)


def test_write_ops_are_audited(motor_hal, audit_records):
    motor_hal.get_device("m").spin(250)
    [rec] = audit_records
    assert rec.event == "audit" and rec.op == "spin" and rec.outcome == "ok"
    assert rec.id == "m" and rec.duration_ms >= 0


def test_reads_are_not_audited(motor_hal, audit_records):
    motor_hal.get_device("m").get_rpm()
    assert audit_records == []


def test_bus_helpers_are_not_audited(audit_records):
    with shal.load(SETUP) as hal:
        hal.get_node("bench").driver.model_for(0x48)  # bus helper, not a device op
    assert audit_records == []
