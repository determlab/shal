"""End-to-end: a driver speaks to a REAL TCP server through the shal,tcp bus,
loaded from a YAML file. Exercises the MessageTransport stack, connection caching
(one socket for many calls), and that teardown actually closes the socket — all
observed from the server side, not mocked.
"""
import json
import socketserver
import textwrap
import threading

import pytest

import shal


@shal.register
class TcpThermostat(shal.Driver):
    compatible = "e2e,tcp-thermostat"
    kind = shal.MessageTransport

    @shal.idempotent
    def read_celsius(self) -> float:
        return self.bus.exchange(self.addr, {"cmd": "temp"})["value"]

    def set_setpoint(self, value: float) -> bool:
        return self.bus.exchange(self.addr, {"cmd": "set", "value": value})["ok"]


class _Thermostat(socketserver.StreamRequestHandler):
    def handle(self):
        # one handler invocation == one accepted connection
        type(self).server.shal_connections += 1
        for line in self.rfile:
            payload = json.loads(line)["payload"]
            if payload["cmd"] == "temp":
                out = {"value": 21.5}
            elif payload["cmd"] == "set":
                out = {"ok": True, "value": payload["value"]}
            else:
                out = {"ok": False}
            self.wfile.write((json.dumps(out) + "\n").encode())
            self.wfile.flush()


@pytest.fixture
def server():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Thermostat)
    srv.shal_connections = 0
    _Thermostat.server = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.fixture
def hal(server, tmp_path):
    port = server.server_address[1]
    setup = tmp_path / "setup.yaml"
    setup.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          net:
            id: net
            driver: shal,tcp
            address: 127.0.0.1:{port}
            insecure: true
            children:
              hvac: {{ id: hvac, driver: "e2e,tcp-thermostat", address: zone1 }}
    """), encoding="utf-8")
    h = shal.load(setup)
    try:
        yield h, server
    finally:
        h.close()


def test_roundtrip_over_real_socket(hal):
    h, _ = hal
    assert h.get_device("hvac").read_celsius() == 21.5
    assert h.get_device("hvac").set_setpoint(19.0) is True


def test_connection_is_cached_across_calls(hal):
    h, server = hal
    dev = h.get_device("hvac")
    for _ in range(5):
        dev.read_celsius()
    assert server.shal_connections == 1     # one socket served all five calls


def test_teardown_closes_then_reconnects(hal):
    h, server = hal
    bus = h.get_node("net").driver
    h.get_device("hvac").read_celsius()
    assert bus.is_active() and server.shal_connections == 1

    bus.close()                              # teardown of this hop
    assert not bus.is_active()

    h.get_device("hvac").read_celsius()      # a fresh call must reconnect end to end
    assert server.shal_connections == 2
