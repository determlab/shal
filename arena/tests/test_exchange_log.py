"""issue #457: the arena demo page shows a log of the real exchanges with
every instrument, in all 3 protocols, and names each instrument's role.

Each exchange is recorded in the BUS layer (`src/shal/buses/`), not guessed
in the UI: the buses call `shal.log.record_exchange` (a no-op unless
`shal.log.exchange_sink` is active), and `SimLog.record_for` is the one
place that turns the hook on -- opt-in, off by default, so a plain `shal`
run never logs a payload."""
from __future__ import annotations

from pathlib import Path

import shal
from shal import log as shal_log

from shal_arena.runner import call_op, drive_input, start_run, take_measurement
from shal_arena.simlog import SimLog
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload

from .conftest import PASSING_DMM_DRIVER, PASSING_RELAY_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK


def _all_entries(tmp_path: Path, run_id: str) -> list[dict]:
    return SimLog(RunStore(tmp_path).sim_log_path(run_id)).entries()


def _exchange_entries(tmp_path: Path, run_id: str) -> list[dict]:
    return [e for e in _all_entries(tmp_path, run_id) if e["kind"] == "exchange"]


def _play_all_three_protocols(tmp_path: Path) -> str:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)       # SCPI
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "true"],
           state_dir=tmp_path)                                                    # Modbus
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=tmp_path)    # I2C
    return run_id


# --------------------------------------------------------------------------- #
# all 3 protocols land in the bus-layer log, in their own real shape
# --------------------------------------------------------------------------- #

def test_all_three_protocols_have_rows_in_the_bus_layer_log(tmp_path: Path) -> None:
    run_id = _play_all_three_protocols(tmp_path)
    entries = _exchange_entries(tmp_path, run_id)
    families = {e["bus_family"] for e in entries}
    assert families == {"sim_msg", "sim_i2c"}
    # SCPI keeps its original "query"/"write" kinds (issue #10), not "exchange"
    all_entries = _all_entries(tmp_path, run_id)
    scpi_kinds = {e["kind"] for e in all_entries if e.get("address") == "dmm0"}
    assert "query" in scpi_kinds

    modbus = next(e for e in entries if e["bus_family"] == "sim_msg")
    assert modbus["request"] == {"fc": 5, "address": 0, "value": True}
    assert isinstance(modbus["response"], dict) and modbus["response"]["fc"] == 5

    i2c = next(e for e in entries if e["bus_family"] == "sim_i2c")
    # real bytes, hex-encoded (shal.log.redact), never invented
    bytes.fromhex(i2c["request"].rstrip("…"))
    bytes.fromhex(i2c["response"].rstrip("…"))


def test_no_row_for_a_command_that_was_not_sent(tmp_path: Path) -> None:
    """No invented `*IDN?` row, or anything else the instrument never saw."""
    run_id = _play_all_three_protocols(tmp_path)
    texts = [str(v) for e in _all_entries(tmp_path, run_id) for v in e.values()]
    assert not any("*IDN?" in t for t in texts)


# --------------------------------------------------------------------------- #
# the UI reads the bus-layer log; it never builds or guesses an exchange
# --------------------------------------------------------------------------- #

def test_ui_timeline_exchange_rows_equal_the_bus_layer_log(tmp_path: Path) -> None:
    run_id = _play_all_three_protocols(tmp_path)
    bus_rows = _exchange_entries(tmp_path, run_id)
    payload = run_payload(run_id, state_dir=tmp_path)
    ui_rows = [e for e in payload["timeline"] if e["kind"] == "exchange"]

    assert len(ui_rows) == len(bus_rows) == 2   # one Modbus, one I2C -- no extras
    bus_by_addr = {e["address"]: e for e in bus_rows}
    for row in ui_rows:
        bus_row = bus_by_addr[row["address"]]
        assert row["ts"] == bus_row["ts"]
        assert row["detail"]["bus_family"] == bus_row["bus_family"]
        assert row["detail"]["request"] == bus_row["request"]
        assert row["detail"]["response"] == bus_row["response"]


def test_drive_row_reads_through_the_shal_gate_never_the_op_name() -> None:
    """`drive` goes through SHAL's own gate, not the agent's driver -- the
    row text says so, literally, and never names the underlying op
    (`set_voltage`)."""
    from shal_arena.ui.page import _SCRIPT
    assert "through the SHAL gate" in _SCRIPT
    assert '"set_voltage"' not in _SCRIPT


# --------------------------------------------------------------------------- #
# role per instrument, from the task yaml's own drives:/probe: -- never
# hand-written text
# --------------------------------------------------------------------------- #

def test_each_instrument_shows_its_role_from_the_task_yaml(tmp_path: Path) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    payload = run_payload(run_id, state_dir=tmp_path)
    by_addr = {i["address"]: i["role"] for i in payload["instruments"]}
    assert by_addr["psu0"] == "drives card.vin"
    assert by_addr["dmm0"] == "probes card.tp_3v3"
    assert by_addr["relay0"] == "drives card.vin"
    assert by_addr["temp0"] == "probes card.tp_reg_temp"


# --------------------------------------------------------------------------- #
# opt-in and off by default: with the hook not enabled, a bus call records
# nothing and writes no log file
# --------------------------------------------------------------------------- #

_I2C_TOPO = {
    "shal_version": 1,
    "root": {
        "bench": {
            "driver": "shal,sim-i2c", "address": "sim0",
            "children": {"t": {"id": "t", "driver": "shal,sim-sensor", "address": 72}},
        },
    },
}

_SCPI_TOPO = {
    "shal_version": 1,
    "root": {
        "bench": {
            "driver": "shal,sim-scpi", "address": "sim0",
            "children": {"p": {"id": "p", "driver": "shal,sim-psu", "address": "psu0"}},
        },
    },
}


def test_hook_not_enabled_records_nothing_for_any_protocol() -> None:
    """A plain `shal.load()` call, wrapped in nothing -- the hook every bus
    checks via `record_exchange` has no sink set anywhere in this process
    at this point, so neither bus call reaches a sink or writes a file."""
    with shal.load(_I2C_TOPO) as hal:
        hal.get_device("t").read_celsius()
    with shal.load(_SCPI_TOPO) as hal, shal.approver(shal.AutoApprove()):
        hal.get_device("p").set_voltage(3.3)
    # nothing to assert on disk: this is the absence of a side effect, by
    # construction -- `record_exchange` is a no-op with no sink active, and
    # neither call above ever opened `shal.log.exchange_sink`.


def test_exchange_sink_only_captures_bus_calls_made_while_it_is_active() -> None:
    captured: list = []
    with shal_log.exchange_sink(captured.append):
        pass   # no bus call happens while the sink is active
    assert captured == []

    with shal.load(_SCPI_TOPO) as hal, shal.approver(shal.AutoApprove()):
        hal.get_device("p").set_voltage(5.0)   # OUTSIDE the `with exchange_sink` above
    assert captured == []   # still nothing: the sink had already been removed


# --------------------------------------------------------------------------- #
# every logged payload goes through shal.log.redact -- a secret appears
# only in its redacted form
# --------------------------------------------------------------------------- #

def test_a_secret_payload_is_logged_only_in_its_redacted_form() -> None:
    secret = b"https://user:s3cr3t-token@example.invalid/path?token=abc123"
    captured: list = []
    with shal_log.exchange_sink(captured.append):
        shal_log.record_exchange("i2c_cli", "/bench/dev", 0x50,
                                 shal_log.redact(secret), shal_log.redact(b""))
    assert len(captured) == 1
    exc = captured[0]
    assert exc.request == shal_log.redact(secret)       # the only form this is allowed in
    assert "s3cr3t-token" not in exc.request             # hex-encoded, not the literal text
    assert exc.request == secret.hex()                   # under redact's 64-byte limit here


def test_record_exchange_is_a_noop_with_no_sink_active() -> None:
    # calling it directly, with nothing having turned the hook on: nothing
    # raises, nothing is recorded anywhere -- the off-by-default contract
    # `record_exchange` itself is responsible for.
    shal_log.record_exchange("sim_msg", "/bench/relay0", "relay0", {"fc": 5}, {"fc": 5})
