"""shal,sim-scpi and shal,sim-msg — generic simulated transports so SCPI and
message/HTTP-style drivers validate with zero hardware (issue #10: every
benchmark driver is proven against a simulated transport)."""
import textwrap

import pytest

import shal
from shal.buses.sim_msg import SimMsgBus, msg_sim_model
from shal.buses.sim_scpi import SimScpiBus, scpi_sim_model
from shal.node import Node
from shal.transport import Write


def write(tmp_path, body: str):
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ---- sim-scpi: the bundled dp832 model round-trips through real driver code -------

SCPI_YAML = """
    shal_version: 1
    root:
      bench:
        id: bench
        driver: shal,sim-scpi
        address: sim0
        children:
          ch1: {id: ch1, driver: "rigol,dp832", address: 1}
"""


def test_scpi_driver_roundtrip_through_sim(tmp_path):
    with shal.load(write(tmp_path, SCPI_YAML)) as hal:
        psu = hal.get_device("ch1")
        psu.set_voltage(3.3)
        assert psu.read_voltage() == pytest.approx(3.3)
        psu.output(True)
        model = hal.get_node("bench").driver.model_for(1)
        assert model.output_on is True


def test_scpi_idempotent_read_survives_transient_drop(tmp_path):
    with shal.load(write(tmp_path, SCPI_YAML)) as hal:
        psu = hal.get_device("ch1")
        psu.set_voltage(1.5)
        hal.get_node("bench").driver.fail_next = 1
        assert psu.read_voltage() == pytest.approx(1.5)   # reconnect once, retry once


def test_scpi_custom_model_registers_like_sim_i2c(tmp_path):
    @scpi_sim_model("test,scpi-widget")
    class WidgetModel:
        def scpi(self, cmd: str) -> str:
            return "42" if cmd.endswith("?") else ""

    @shal.register
    class Widget(shal.Driver):  # noqa
        compatible = "test,scpi-widget"
        kind = shal.MessageTransport
        llm_ready = True

        @shal.idempotent
        @shal.op("Read the widget value now.", side_effect="none")
        def read_value(self) -> int:
            return int(self.bus.exchange(self.addr,
                                         {"scpi": "VAL?", "query": True})["reply"])

    p = write(tmp_path, """
        shal_version: 1
        root:
          bench:
            driver: shal,sim-scpi
            address: sim0
            children:
              w: {id: w, driver: "test,scpi-widget", address: inst1}
    """)
    with shal.load(p) as hal:
        assert hal.get_device("w").read_value() == 42


# ---- sim-msg: scripted MessageTransport for HTTP/cloud-style drivers ---------------

def test_msg_driver_roundtrip_through_sim(tmp_path):
    @msg_sim_model("test,msg-widget")
    class EchoModel:
        def handle(self, msg) -> dict:
            if msg.get("cmd") == "status":
                return {"ok": True, "state": "idle"}
            return {"ok": False, "error": "unknown"}

    @shal.register
    class MsgWidget(shal.Driver):  # noqa
        compatible = "test,msg-widget"
        kind = shal.MessageTransport
        llm_ready = True

        @shal.idempotent
        @shal.op("Read the device state now.", side_effect="none")
        def read_state(self) -> str:
            return self.bus.exchange(self.addr, {"cmd": "status"})["state"]

    p = write(tmp_path, """
        shal_version: 1
        root:
          svc:
            driver: shal,sim-msg
            address: sim0
            children:
              w: {id: w, driver: "test,msg-widget", address: dev1}
    """)
    with shal.load(p) as hal:
        assert hal.get_device("w").read_state() == "idle"


def test_msg_unknown_address_is_a_hop_error(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          svc: {id: svc, driver: "shal,sim-msg", address: sim0}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("svc").driver
        with pytest.raises(shal.HopError):
            bus.exchange("ghost", {"cmd": "status"})


class _Opaque:
    """Stand-in for a value reaching a str/int-only grammar check as some
    other type — its ``str()`` is what a caller might see leak."""

    def __init__(self, s: str) -> None:
        self._s = s

    def __str__(self) -> str:
        return self._s


def test_sim_msg_child_address_error_redacts_credentials():
    # issue #126: the label grammar check only rejects on TYPE, so any
    # non-str/int value reaching it must still have its stringified content
    # redacted before it is echoed
    node = Node("svc", address="sim0")
    bus = SimMsgBus(node)
    bad = _Opaque("https://user:secret@evil.example/x?token=abc123")
    with pytest.raises(shal.LoadError, match="service/device label") as ei:
        bus.validate_address(bad)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_sim_msg_child_address_error_keeps_plain_value():
    # a non-credential bad value must still be echoed in full — redaction
    # must not over-mask ordinary debugging info
    node = Node("svc", address="sim0")
    bus = SimMsgBus(node)
    with pytest.raises(shal.LoadError, match="service/device label") as ei:
        bus.validate_address(["not-a-label"])
    assert "not-a-label" in str(ei.value)


def test_sim_msg_unknown_address_redacts_credentials(tmp_path):
    # issue #126: the runtime addr passed to exchange() can be any resolved
    # string, credential-shaped or not — a value with no matching model must
    # not echo a credential verbatim
    p = write(tmp_path, """
        shal_version: 1
        root:
          svc: {id: svc, driver: "shal,sim-msg", address: sim0}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("svc").driver
        with pytest.raises(shal.HopError, match="no service at") as ei:
            bus.exchange("https://user:secret@evil.example/x?token=abc123",
                         {"cmd": "status"})
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_sim_msg_unknown_address_keeps_plain_value(tmp_path):
    # a non-credential bad value (a typo'd device label) must still be
    # echoed in full — redaction must not over-mask ordinary debugging info
    p = write(tmp_path, """
        shal_version: 1
        root:
          svc: {id: svc, driver: "shal,sim-msg", address: sim0}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("svc").driver
        with pytest.raises(shal.HopError, match="no service at") as ei:
            bus.exchange("ghost-device", {"cmd": "status"})
    assert "ghost-device" in str(ei.value)


def test_sim_scpi_child_address_error_redacts_credentials():
    # issue #126: the label grammar check only rejects on TYPE, so any
    # non-str/int value reaching it must still have its stringified content
    # redacted before it is echoed
    node = Node("bench", address="sim0")
    bus = SimScpiBus(node)
    bad = _Opaque("https://user:secret@evil.example/x?token=abc123")
    with pytest.raises(shal.LoadError, match="instrument/channel label") as ei:
        bus.validate_address(bad)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_sim_scpi_child_address_error_keeps_plain_value():
    # a non-credential bad value must still be echoed in full — redaction
    # must not over-mask ordinary debugging info
    node = Node("bench", address="sim0")
    bus = SimScpiBus(node)
    with pytest.raises(shal.LoadError, match="instrument/channel label") as ei:
        bus.validate_address(["not-a-label"])
    assert "not-a-label" in str(ei.value)


def test_sim_scpi_unknown_instrument_redacts_credentials(tmp_path):
    # issue #126: the runtime addr passed to exchange() can be any resolved
    # string, credential-shaped or not — a value with no matching model must
    # not echo a credential verbatim
    p = write(tmp_path, """
        shal_version: 1
        root:
          bench: {id: bench, driver: "shal,sim-scpi", address: sim0}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("bench").driver
        with pytest.raises(shal.HopError, match="no instrument at") as ei:
            bus.exchange("https://user:secret@evil.example/x?token=abc123",
                         {"scpi": "*IDN?", "query": True})
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_sim_scpi_unknown_instrument_keeps_plain_value(tmp_path):
    # a non-credential bad value (a typo'd instrument label) must still be
    # echoed in full — redaction must not over-mask ordinary debugging info
    p = write(tmp_path, """
        shal_version: 1
        root:
          bench: {id: bench, driver: "shal,sim-scpi", address: sim0}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("bench").driver
        with pytest.raises(shal.HopError, match="no instrument at") as ei:
            bus.exchange("ghost-instrument", {"scpi": "*IDN?", "query": True})
    assert "ghost-instrument" in str(ei.value)


# ---- the bus contract for routes (#238): "no" only when nothing was sent ------------

_CALLS = {
    "shal,sim-i2c": lambda b: b.txn(0x48, [Write(b"\x00")]),
    "shal,sim-scpi": lambda b: b.exchange("x", {"scpi": "*IDN?", "query": True}),
    "shal,sim-msg": lambda b: b.exchange("x", {"cmd": "status"}),
}


@pytest.mark.parametrize("driver", sorted(_CALLS))
def test_sim_bus_reports_before_send_as_no_and_after_send_as_unknown(tmp_path, driver):
    p = write(tmp_path, f"""
        shal_version: 1
        root:
          bus: {{id: bus, driver: "{driver}", address: sim0}}
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("bus").driver
        bus.fail_next = 1                      # dropped BEFORE the request went out
        with pytest.raises(shal.HopError) as ei:
            _CALLS[driver](bus)
        assert ei.value.delivered == "no"
        bus.fail_delivered_unknown = True      # lost AFTER the request went out
        with pytest.raises(shal.HopError) as ei:
            _CALLS[driver](bus)
        assert ei.value.delivered == "unknown"
