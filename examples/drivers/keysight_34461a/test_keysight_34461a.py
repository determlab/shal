"""keysight,34461a over the shal,scpi-raw bus against a fake SCPI socket instrument.

Moved from the core suite with the driver (#149). There is no sim twin for this
meter (nothing is registered for it in sim_scpi), so it is tested against a small
socket server that speaks line SCPI. Run: pytest examples/drivers
"""
import os
import socketserver
import sys
import textwrap
import threading

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import keysight_34461a_driver  # noqa: E402,F401  (registers keysight,34461a)

import shal  # noqa: E402


class _Meter(socketserver.StreamRequestHandler):
    """A minimal DMM: every query returns one fixed value line."""

    REPLIES = {"MEAS:VOLT:DC?": "1.234560", "MEAS:CURR:DC?": "0.010000",
               "MEAS:RES?": "99.500000"}

    def handle(self):
        for raw in self.rfile:
            cmd = raw.decode().strip()
            if "?" in cmd:
                self.wfile.write((self.REPLIES.get(cmd, "0") + "\n").encode())
                self.wfile.flush()


@pytest.fixture
def hal(tmp_path):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Meter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    p = tmp_path / "bench.yaml"
    p.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          bench:
            id: bench
            driver: shal,scpi-raw
            address: 127.0.0.1:{srv.server_address[1]}
            insecure: true
            children:
              meter: {{ id: dmm, driver: "keysight,34461a", address: dmm }}
    """), encoding="utf-8")
    h = shal.load(p)
    try:
        yield h
    finally:
        h.close()
        srv.shutdown()
        srv.server_close()


def test_dmm_measurements(hal):
    dmm = hal.get_device("dmm")
    assert dmm.measure_voltage_dc() == pytest.approx(1.23456)
    assert dmm.measure_current_dc() == pytest.approx(0.01)
    assert dmm.measure_resistance() == pytest.approx(99.5)
    assert isinstance(dmm, shal.DigitalMultimeter)


def test_dmm_in_catalog():
    dmm = shal.catalog("keysight,34461a")
    assert dmm["capability"] == "DigitalMultimeter"
    assert {o["name"] for o in dmm["ops"]} == {
        "measure_voltage_dc", "measure_current_dc", "measure_resistance"}
