"""shal,sim-scpi and shal,sim-msg — generic simulated transports so SCPI and
message/HTTP-style drivers validate with zero hardware (issue #10: every
benchmark driver is proven against a simulated transport)."""
import textwrap

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model
from shal.buses.sim_scpi import scpi_sim_model


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
