"""issue #457: the arena demo page shows a log of the real exchanges with
every instrument, in all 3 protocols, and names each instrument's role.

Each exchange is recorded in the BUS layer (`src/shal/buses/`), not guessed
in the UI: the buses call `shal.log.record_exchange` (a no-op unless
`shal.log.exchange_sink` is active -- checked first, before any redaction
work), and `SimLog.record_for` is the one place that turns the hook on --
opt-in, off by default, so a plain `shal` run never logs a payload.

CTO review round 2: redaction moved into `record_exchange` itself (the one
place every bus calls), not copied per bus -- the tests below run secrets
through the real buses, never call `record_exchange` with an
already-redacted value directly."""
from __future__ import annotations

from pathlib import Path

import pytest
import shal
from shal import log as shal_log
from shal.buses.sim import sim_model
from shal.buses.sim_msg import msg_sim_model
from shal.buses.sim_scpi import scpi_sim_model
from shal.transport import ByteTransport, MessageTransport, Read, Write

from shal_arena.runner import call_op, drive_input, start_run, take_measurement
from shal_arena.simlog import SimLog
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload

from .conftest import PASSING_DMM_DRIVER, PASSING_RELAY_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK


def _register_dummy_driver(compatible: str, kind: type) -> None:
    """A minimal Driver for a test-only compatible -- the loader binds
    EVERY child to a registered Driver class at load time regardless of
    whether a device is ever looked up, so a sim model alone is not
    enough to load a topology naming it."""
    cls = type(compatible.replace(",", "_"), (shal.Driver,), {
        "compatible": compatible, "kind": kind, "llm_ready": True})
    shal.registry.register(cls, override=True)


def _all_entries(tmp_path: Path, run_id: str) -> list[dict]:
    return SimLog(RunStore(tmp_path).sim_log_path(run_id)).entries()


def _exchange_entries(tmp_path: Path, run_id: str) -> list[dict]:
    return [e for e in _all_entries(tmp_path, run_id) if e["kind"] == "exchange"]


def _bus_layer_entries(tmp_path: Path, run_id: str) -> list[dict]:
    """Every row the exchange hook itself produced: `exchange` (Modbus,
    I2C), and SCPI's own `query`/`write` -- told apart from CardSim's
    unrelated `write` (a clean drive, issue #427) by the `cmd` key, which
    only a SCPI exchange row carries."""
    return [e for e in _all_entries(tmp_path, run_id)
           if e["kind"] == "exchange" or ("cmd" in e and e["kind"] in ("query", "write"))]


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

def test_ui_timeline_rows_equal_the_bus_layer_log_for_every_protocol(tmp_path: Path) -> None:
    """CTO review round 2: covers every bus-layer row, SCPI included --
    not only `exchange`-kind rows, which would never catch a missing
    `reply` on the SCPI side."""
    run_id = _play_all_three_protocols(tmp_path)
    bus_rows = _bus_layer_entries(tmp_path, run_id)
    payload = run_payload(run_id, state_dir=tmp_path)
    ui_rows = [e for e in payload["timeline"]
              if e["kind"] == "exchange"
              or ("cmd" in e["detail"] and e["kind"] in ("query", "write"))]

    assert len(ui_rows) == len(bus_rows) == 3   # SCPI query, Modbus, I2C -- no extras
    bus_by = {(e["address"], e["kind"]): e for e in bus_rows}
    for row in ui_rows:
        bus_row = bus_by[(row["address"], row["kind"])]
        assert row["ts"] == bus_row["ts"]
        if row["kind"] == "exchange":
            assert row["detail"]["bus_family"] == bus_row["bus_family"]
            assert row["detail"]["request"] == bus_row["request"]
            assert row["detail"]["response"] == bus_row["response"]
        else:
            assert row["detail"]["cmd"] == bus_row["cmd"]
            assert row["detail"]["reply"] == bus_row["reply"]


def test_drive_row_reads_through_the_shal_gate_never_the_op_name(tmp_path: Path) -> None:
    """`drive` goes through SHAL's own gate, not the agent's driver. No JS
    engine here, so this checks both halves of the real claim: a real
    `drive_input` call leaves exactly the entry (`kind: "write"`, a
    `volts` field) the page's own JS branches on to print "drive ... V,
    through the SHAL gate" -- and that literal phrase is the one the
    shipped JS source actually contains for that branch, never the
    underlying op name."""
    from shal_arena.ui.page import _SCRIPT

    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=tmp_path)
    payload = run_payload(run_id, state_dir=tmp_path)
    drives = [e for e in payload["timeline"]
             if e["kind"] == "write" and "volts" in e["detail"]]
    assert len(drives) == 1 and drives[0]["detail"]["volts"] == 12.0

    assert 'return v === undefined ? `Wrote ${addr}` : ' in _SCRIPT
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
    # the agent-facing JSON (the raw drives:/probe: fields, and this role
    # string built from them) is unchanged by the scope add below.
    assert by_addr["psu0"] == "drives card.vin"
    assert by_addr["dmm0"] == "probes card.tp_3v3"
    assert by_addr["relay0"] == "drives card.vin"
    assert by_addr["temp0"] == "probes card.tp_reg_temp"


def test_role_text_data_matches_the_pages_plain_words_rule(tmp_path: Path) -> None:
    """issue #457 scope add: the page renders the role as plain words --
    "powers VIN", "measures the 3V3 rail", "measures the regulator
    temperature" -- built client-side from `drives`/`probe` plus
    `payload.rails`/`payload.temp_points`. No JS engine here, so this
    proves the DATA the JS's own rule (checked below, in `_SCRIPT`) needs
    is present and correct: the rail's `name` is already uppercase
    ("3V3"), the temp point's `name` is "regulator", and both `test_point`
    values match what `probe` names."""
    from shal_arena.ui.page import _SCRIPT

    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    payload = run_payload(run_id, state_dir=tmp_path)
    rail = next(r for r in payload["rails"] if r["test_point"] == "tp_3v3")
    assert rail["name"] == "3V3"
    temp = next(t for t in payload["temp_points"] if t["test_point"] == "tp_reg_temp")
    assert temp["name"] == "regulator"

    assert "powers ${name.toUpperCase()}" in _SCRIPT
    assert "measures the ${rail.name} rail" in _SCRIPT
    assert "measures the ${temp.name} temperature" in _SCRIPT


# --------------------------------------------------------------------------- #
# opt-in and off by default: with the hook not enabled, a bus call records
# nothing and writes no log file -- checked for all 3 protocols
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

@msg_sim_model("test,no-sink-msg")
class _NoSinkMsgModel:
    def handle(self, msg: dict) -> dict:
        return {"ok": True}


_register_dummy_driver("test,no-sink-msg", MessageTransport)

_MSG_TOPO = {
    "shal_version": 1,
    "root": {
        "bench": {
            "id": "bench", "driver": "shal,sim-msg", "address": "sim0",
            "children": {"r": {"id": "r", "driver": "test,no-sink-msg", "address": "relay1"}},
        },
    },
}


def test_hook_not_enabled_records_nothing_for_any_protocol(tmp_path: Path,
                                                            monkeypatch) -> None:
    """CTO review round 2: real asserts, not just an untouched `tmp_path` --
    that held even with the hook's off-check removed entirely, so it
    proved nothing. Wraps each bus module's own `record_exchange` (still
    calling the real function) to prove the hook was actually reached for
    all 3 protocols, and spies on `_clean_payload` AND `Exchange` itself
    (round 3 nit: a `_clean_payload` spy alone does not prove nothing was
    RECORDED -- a sink-less implementation that saved the raw values
    somewhere else would still pass it) to prove neither does any work."""
    import shal.buses.sim as sim_mod
    import shal.buses.sim_msg as sim_msg_mod
    import shal.buses.sim_scpi as sim_scpi_mod

    monkeypatch.chdir(tmp_path)
    counts = {"sim": 0, "sim_scpi": 0, "sim_msg": 0}
    clean_calls = []
    exchange_calls = []

    def _counted(mod, key, real):
        def wrapper(*args, **kwargs):
            counts[key] += 1
            return real(*args, **kwargs)
        monkeypatch.setattr(mod, "record_exchange", wrapper)

    _counted(sim_mod, "sim", sim_mod.record_exchange)
    _counted(sim_scpi_mod, "sim_scpi", sim_scpi_mod.record_exchange)
    _counted(sim_msg_mod, "sim_msg", sim_msg_mod.record_exchange)
    monkeypatch.setattr(shal_log, "_clean_payload",
                        lambda v: clean_calls.append(v) or v)
    real_exchange = shal_log.Exchange
    monkeypatch.setattr(shal_log, "Exchange",
                        lambda *a, **kw: exchange_calls.append((a, kw)) or real_exchange(*a, **kw))

    with shal.load(_I2C_TOPO) as hal:
        hal.get_device("t").read_celsius()
    with shal.load(_SCPI_TOPO) as hal, shal.approver(shal.AutoApprove()):
        hal.get_device("p").set_voltage(3.3)
    with shal.load(_MSG_TOPO) as hal:
        hal.get_node("bench").driver.exchange("relay1", {"fc": 5, "address": 0, "value": True})

    assert counts == {"sim": 1, "sim_scpi": 1, "sim_msg": 1}   # the hook really ran
    assert clean_calls == []                                   # but never redacted anything
    assert exchange_calls == []                                 # and never built a record
    assert shal_log._exchange_sink.get() is None
    assert list(tmp_path.iterdir()) == []   # cwd, via monkeypatch.chdir above


def test_exchange_sink_only_captures_bus_calls_made_while_it_is_active(monkeypatch) -> None:
    clean_calls = []
    monkeypatch.setattr(shal_log, "_clean_payload",
                        lambda v: clean_calls.append(v) or v)

    captured: list = []
    with shal_log.exchange_sink(captured.append):
        pass   # no bus call happens while the sink is active
    assert captured == []

    with shal.load(_SCPI_TOPO) as hal, shal.approver(shal.AutoApprove()):
        hal.get_device("p").set_voltage(5.0)   # OUTSIDE the `with exchange_sink` above
    assert captured == []   # still nothing: the sink had already been removed
    assert clean_calls == []   # and no redaction work happened for this call either


# --------------------------------------------------------------------------- #
# every logged payload passes through record_exchange's own redaction --
# a secret appears only in its redacted form. Run through the REAL buses
# (CTO review round 2), never by calling record_exchange with an
# already-redacted value directly.
# --------------------------------------------------------------------------- #

_SECRET_URL = "https://user:s3cr3t-token@example.invalid/path?token=abc123"


@scpi_sim_model("test,secret-scpi")
class _SecretScpiModel:
    def scpi(self, cmd: str) -> str:
        return _SECRET_URL


@msg_sim_model("test,secret-msg")
class _SecretMsgModel:
    def handle(self, msg: dict) -> dict:
        return {"endpoint": _SECRET_URL}


@sim_model("test,secret-i2c")
class _SecretI2cModel:
    def txn(self, ops) -> bytes:
        return _SECRET_URL.encode()


_register_dummy_driver("test,secret-scpi", MessageTransport)
_register_dummy_driver("test,secret-msg", MessageTransport)
_register_dummy_driver("test,secret-i2c", ByteTransport)


def test_a_secret_scpi_reply_is_logged_only_in_redacted_form() -> None:
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-scpi", "address": "sim0",
        "children": {"p": {"id": "p", "driver": "test,secret-scpi", "address": "psu1"}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        hal.get_node("bench").driver.exchange("psu1", {"scpi": "GET?", "query": True})
    (exc,) = captured
    assert "s3cr3t-token" not in exc.response and "token=abc123" not in exc.response
    assert exc.response == shal_log.redact_url(_SECRET_URL)


# -- CTO review round 2: the text rule must clean ONLY the URL substring,
# never mangle ordinary text (a real SCPI channel list) or leave userinfo
# behind depending on where in the string the URL falls -----------------

_EMBEDDED_SECRET_URL = "ENDPOINT https://user:s3cr3t-token@example.invalid/path?token=abc123 OK"
_CHANNEL_LIST_REPLY = "MEAS:VOLT? (@1)"


@scpi_sim_model("test,secret-scpi-embedded")
class _SecretScpiEmbeddedModel:
    def scpi(self, cmd: str) -> str:
        return _EMBEDDED_SECRET_URL


@scpi_sim_model("test,scpi-channel-list")
class _ScpiChannelListModel:
    def scpi(self, cmd: str) -> str:
        return _CHANNEL_LIST_REPLY


_register_dummy_driver("test,secret-scpi-embedded", MessageTransport)
_register_dummy_driver("test,scpi-channel-list", MessageTransport)


def test_an_embedded_secret_url_is_redacted_with_no_userinfo_left(tmp_path: Path) -> None:
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-scpi", "address": "sim0",
        "children": {"p": {"id": "p", "driver": "test,secret-scpi-embedded",
                           "address": "psu1"}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        hal.get_node("bench").driver.exchange("psu1", {"scpi": "GET?", "query": True})
    (exc,) = captured
    assert "s3cr3t-token" not in exc.response
    assert "token=abc123" not in exc.response
    assert exc.response == "ENDPOINT https://example.invalid/path OK"


def test_a_real_scpi_channel_list_reply_is_logged_unchanged() -> None:
    """`MEAS:VOLT? (@1)` is a real SCPI channel-list reply on real
    instruments (`scpi_raw`) -- it has no `://` anywhere, so the text rule
    must never touch it (it used to be mangled into `"1)"` by treating the
    whole string as a bare address because it contains `@`)."""
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-scpi", "address": "sim0",
        "children": {"p": {"id": "p", "driver": "test,scpi-channel-list",
                           "address": "psu1"}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        hal.get_node("bench").driver.exchange("psu1", {"scpi": "GET?", "query": True})
    (exc,) = captured
    assert exc.response == _CHANNEL_LIST_REPLY


# -- CTO review round 3: redact_url_in_text must be TOTAL -- a URL substring
# that `redact_url` cannot parse (cut short by the punctuation-stopping
# above, or malformed in the reply itself) must never raise out of a real
# bus call. Turning on the exchange log must never change what the call
# returns ------------------------------------------------------------------

_PORT_QUOTE_REPLY = 'ftp://admin:pw@10.0.0.1:21"'


@scpi_sim_model("test,secret-scpi-port-quote")
class _SecretScpiPortQuoteModel:
    def scpi(self, cmd: str) -> str:
        return _PORT_QUOTE_REPLY


_register_dummy_driver("test,secret-scpi-port-quote", MessageTransport)


def test_a_port_followed_by_a_quote_does_not_raise_and_is_redacted() -> None:
    """A reply of `ftp://admin:pw@10.0.0.1:21"` (a quoted string is normal in
    a SCPI reply) used to make `urlsplit` raise `ValueError` out of the bus
    call itself (the old regex took the closing quote into the port), so
    turning the exchange log on could turn a successful real-instrument op
    into a failure. The call must still return normally, and the logged
    value must carry no userinfo."""
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-scpi", "address": "sim0",
        "children": {"p": {"id": "p", "driver": "test,secret-scpi-port-quote",
                           "address": "psu1"}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        reply = hal.get_node("bench").driver.exchange(
            "psu1", {"scpi": "GET?", "query": True})
    assert reply == {"reply": _PORT_QUOTE_REPLY}  # the real call result is untouched
    (exc,) = captured
    assert "admin" not in exc.response
    assert "pw" not in exc.response
    assert exc.response == 'ftp://10.0.0.1:21"'


@pytest.mark.parametrize("text,expected", [
    ("(http://u:p@h:5025)", "(http://h:5025)"),
    ("see http://u:p@h:5025, ok", "see http://h:5025, ok"),
    ("(http://u:p@h/x?token=a)", "(http://h/x)"),
    ("<http://u:p@h>", "<http://h>"),
    ("http://[::1", "http://<redacted>"),
    ("http://u:p w@h/", "http://<redacted> w@h/"),
])
def test_redact_url_in_text_table(text: str, expected: str) -> None:
    """A direct table test of the helper (CTO review round 3): trailing
    punctuation a URL is normally wrapped in is never swallowed, and a
    substring `redact_url` cannot parse never raises -- it becomes a
    placeholder instead of leaking or propagating."""
    assert shal_log.redact_url_in_text(text) == expected


def test_a_secret_modbus_value_is_logged_only_in_redacted_form() -> None:
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-msg", "address": "sim0",
        "children": {"r": {"id": "r", "driver": "test,secret-msg", "address": "svc1"}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        hal.get_node("bench").driver.exchange("svc1", {"cmd": "status"})
    (exc,) = captured
    assert "s3cr3t-token" not in exc.response["endpoint"]
    assert exc.response["endpoint"] == shal_log.redact_url(_SECRET_URL)


def test_a_secret_i2c_payload_is_logged_only_in_redacted_form() -> None:
    topo = {"shal_version": 1, "root": {"bench": {
        "id": "bench", "driver": "shal,sim-i2c", "address": "sim0",
        "children": {"d": {"id": "d", "driver": "test,secret-i2c", "address": 72}}}}}
    captured: list = []
    with shal.load(topo) as hal, shal_log.exchange_sink(captured.append):
        hal.get_node("bench").driver.txn(72, [Write(bytes([0])), Read(len(_SECRET_URL))])
    (exc,) = captured
    # hex-encoded (shal.log.redact): never the literal secret text
    assert "s3cr3t-token" not in exc.response
    assert exc.response == shal_log.redact(_SECRET_URL.encode())


def test_record_exchange_is_a_noop_with_no_sink_active() -> None:
    # calling it directly, with nothing having turned the hook on: nothing
    # raises, nothing is recorded anywhere -- the off-by-default contract
    # `record_exchange` itself is responsible for.
    shal_log.record_exchange("sim_msg", "/bench/relay0", "relay0", {"fc": 5}, {"fc": 5})

