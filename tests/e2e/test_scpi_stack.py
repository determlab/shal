"""End-to-end: a PSU and a DMM driven over the shal,scpi-raw bus against a REAL
fake SCPI instrument (a socket server speaking line SCPI), loaded from YAML.
Exercises the MessageTransport stack, connection caching, the plaintext
`insecure` gate, and write-then-query state — observed server-side, not mocked.
"""
import socketserver
import textwrap
import threading

import pytest

import shal


class _Instrument(socketserver.StreamRequestHandler):
    """A minimal SCPI instrument: write commands mutate state, query commands
    (trailing '?') return one value line. Serves both PSU and DMM command sets."""

    def handle(self):
        srv = type(self).server
        srv.shal_connections += 1
        st = srv.state
        for raw in self.rfile:
            cmd = raw.decode().strip()
            if not cmd:
                continue
            if "?" in cmd:   # SCPI: any command containing '?' is a query

                if cmd.startswith(":MEAS:VOLT? CH"):
                    resp = f"{st['volt']:.6f}"
                elif cmd.startswith(":MEAS:CURR? CH"):
                    resp = f"{st['curr']:.6f}"
                elif cmd == "MEAS:VOLT:DC?":
                    resp = "1.234560"
                elif cmd == "MEAS:CURR:DC?":
                    resp = "0.010000"
                elif cmd == "MEAS:RES?":
                    resp = "99.500000"
                else:
                    resp = "0"
                self.wfile.write((resp + "\n").encode())
                self.wfile.flush()
            else:  # a write: mutate state, no response
                if cmd.startswith(":SOUR") and ":VOLT" in cmd:
                    st["volt"] = float(cmd.rsplit(" ", 1)[1])
                elif cmd.startswith(":OUTP"):
                    st["output"] = "ON" in cmd


@pytest.fixture
def instrument():
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Instrument)
    srv.shal_connections = 0
    srv.state = {"volt": 0.0, "curr": 0.5, "output": False}
    _Instrument.server = srv
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield srv
    finally:
        srv.shutdown()
        srv.server_close()


def _setup(tmp_path, port) -> str:
    p = tmp_path / "bench.yaml"
    p.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          bench:
            id: bench
            driver: shal,scpi-raw
            address: 127.0.0.1:{port}
            insecure: true
            children:
              psu: {{ id: psu, driver: "rigol,dp832", address: 1 }}
              meter: {{ id: dmm, driver: "keysight,34461a", address: dmm }}
    """), encoding="utf-8")
    return p


@pytest.fixture
def hal(instrument, tmp_path):
    h = shal.load(_setup(tmp_path, instrument.server_address[1]))
    try:
        yield h, instrument
    finally:
        h.close()


def test_psu_set_then_measure(hal):
    h, _ = hal
    psu = h.get_device("psu")
    psu.set_voltage(3.3)                         # write
    assert psu.read_voltage() == pytest.approx(3.3)   # query reflects state
    assert psu.read_current() == pytest.approx(0.5)
    psu.output(True)
    assert isinstance(psu, shal.PowerSupply)


def test_dmm_measurements(hal):
    h, _ = hal
    dmm = h.get_device("dmm")
    assert dmm.measure_voltage_dc() == pytest.approx(1.23456)
    assert dmm.measure_resistance() == pytest.approx(99.5)
    assert isinstance(dmm, shal.DigitalMultimeter)


def test_one_socket_serves_both_devices(hal):
    h, srv = hal
    h.get_device("psu").read_voltage()
    h.get_device("dmm").measure_voltage_dc()
    assert srv.shal_connections == 1            # connection cached across devices


def test_scpi_requires_insecure(tmp_path, instrument):
    p = tmp_path / "bad.yaml"
    p.write_text(textwrap.dedent(f"""\
        shal_version: 1
        root:
          bench:
            driver: shal,scpi-raw
            address: 127.0.0.1:{instrument.server_address[1]}
            children:
              psu: {{ id: psu, driver: "rigol,dp832", address: 1 }}
    """), encoding="utf-8")
    with pytest.raises(shal.LoadError, match="insecure"):
        shal.load(p)


def test_instruments_in_catalog():
    psu = shal.catalog("rigol,dp832")
    assert psu["capability"] == "PowerSupply"
    assert psu["address_schema"]["examples"] == [1]
    dmm = shal.catalog("keysight,34461a")
    assert dmm["capability"] == "DigitalMultimeter"
    assert {o["name"] for o in dmm["ops"]} == {
        "measure_voltage_dc", "measure_current_dc", "measure_resistance"}
