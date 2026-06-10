"""Bus suite: mux (incl. the v1 two-mux regression), local, ssh argv,
i2c-cli rendering, http TLS rule, tcp roundtrip."""
import json
import socketserver
import stat
import sys
import textwrap
import threading
from pathlib import Path

import pytest

import shal
from shal.buses.i2c_cli import parse_output, render_ops
from shal.buses.ssh import ssh_argv
from shal.transport import Read, Write


def write(tmp_path, body: str) -> Path:
    p = tmp_path / "setup.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


MUX_YAML = """
shal_version: 1
root:
  bench:
    id: bench
    driver: shal,sim-i2c
    address: sim0
    children:
      mux0:
        driver: nxp,pca9548
        address: 0x70
        children:
          ch0:
            address: 0
            children:
              dut_a: {id: dut_a, driver: "ti,tmp102", address: 0x48}
          ch1:
            address: 1
            children:
              dut_b: {id: dut_b, driver: "ti,tmp102", address: 0x49}
"""


# ---- mux ----------------------------------------------------------------------

def test_mux_select_is_cached(tmp_path):
    with shal.load(write(tmp_path, MUX_YAML)) as hal:
        a, b = hal.get_device("dut_a"), hal.get_device("dut_b")
        mux = hal.get_node("bench").driver.model_for(0x70)
        a.read_celsius(); a.read_celsius(); a.read_celsius()
        assert mux.select_count == 1          # repeat channel pays nothing
        b.read_celsius()
        assert mux.select_count == 2          # switching re-selects
        a.read_celsius()
        assert mux.select_count == 3


def test_two_muxes_one_upstream_dont_stomp(tmp_path):
    """v1 regression: shared current_channel on the parent bus mis-routed."""
    p = write(tmp_path, """
        shal_version: 1
        root:
          bench:
            id: bench
            driver: shal,sim-i2c
            address: sim0
            children:
              mux0:
                driver: nxp,pca9548
                address: 0x70
                children:
                  ch0:
                    address: 0
                    children:
                      dut_a: {id: dut_a, driver: "ti,tmp102", address: 0x48}
              mux1:
                driver: nxp,pca9548
                address: 0x71
                children:
                  ch0:
                    address: 0
                    children:
                      dut_c: {id: dut_c, driver: "ti,tmp102", address: 0x4a}
    """)
    with shal.load(p) as hal:
        a, c = hal.get_device("dut_a"), hal.get_device("dut_c")
        sim = hal.get_node("bench").driver
        a.read_celsius(); c.read_celsius(); a.read_celsius(); c.read_celsius()
        # per-mux state: interleaving must NOT invalidate the other mux's cache
        assert sim.model_for(0x70).select_count == 1
        assert sim.model_for(0x71).select_count == 1


def test_mux_bad_channel_fails_load(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          bench:
            driver: shal,sim-i2c
            address: sim0
            children:
              mux0:
                driver: nxp,pca9548
                address: 0x70
                children:
                  ch9:
                    address: 9
                    children:
                      d: {driver: "ti,tmp102", address: 0x48}
    """)
    with pytest.raises(shal.LoadError, match="channel must be 0-7"):
        shal.load(p)


def test_mux_downstream_address_grammar(tmp_path):
    p = write(tmp_path, MUX_YAML.replace("address: 0x48", "address: 0x99"))
    with pytest.raises(shal.LoadError, match="0x03-0x77"):
        shal.load(p)


# ---- local + i2c-cli ------------------------------------------------------------

def test_local_runs_argv_no_shell(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          here: {id: here, driver: "shal,local", address: localhost}
    """)
    with shal.load(p) as hal:
        out = hal.get_node("here").driver.run(
            [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
            stdin=b"$(echo pwned); hello",  # shell metacharacters are inert data
        )
        assert out.exit == 0
        assert out.stdout == b"$(echo pwned); hello"


def test_i2c_cli_render_and_parse():
    argv = render_ops(0x48, [Write(b"\x00"), Read(2)])
    assert argv == ["w1@0x48", "0x00", "r2"]      # repeated-start write-then-read
    assert parse_output(b"0x19 0x00\n") == b"\x19\x00"


def test_i2c_cli_bad_device_path(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          here:
            driver: shal,local
            address: localhost
            children:
              i2c0: {driver: "shal,i2c-cli", address: /dev/ttyUSB0}
    """)
    with pytest.raises(shal.LoadError, match="/dev/i2c"):
        shal.load(p)


@pytest.mark.skipif(sys.platform == "win32", reason="executable shim needs POSIX")
def test_i2c_cli_end_to_end_over_local(tmp_path, monkeypatch):
    """The canonical stack — tmp102 -> i2c-cli -> argv -> local exec — against
    a fake i2ctransfer. Validates rendering, carriage, and parsing end-to-end."""
    shim = tmp_path / "bin" / "i2ctransfer"
    shim.parent.mkdir()
    shim.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import sys
        assert sys.argv[1:] == ["-y", "1", "w1@0x48", "0x00", "r2"], sys.argv
        print("0x19 0x00")
    """), encoding="utf-8")
    shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{shim.parent}:{__import__('os').environ['PATH']}")
    p = write(tmp_path, """
        shal_version: 1
        root:
          here:
            driver: shal,local
            address: localhost
            children:
              i2c0:
                driver: shal,i2c-cli
                address: /dev/i2c-1
                children:
                  t: {id: t, driver: "ti,tmp102", address: 0x48}
    """)
    with shal.load(p) as hal:
        assert hal.get_device("t").read_celsius() == pytest.approx(25.0)


# ---- ssh ------------------------------------------------------------------------

def test_ssh_argv_is_a_vector_with_separator():
    argv = ssh_argv("user@rack-a", ["i2ctransfer", "-y", "1", "r2@0x48"])
    assert argv[0] == "ssh" and "user@rack-a" in argv
    sep = argv.index("--")
    assert argv[sep + 1:] == ["i2ctransfer", "-y", "1", "r2@0x48"]
    assert all(isinstance(a, str) for a in argv)  # never a joined shell string


# ---- http TLS rule ---------------------------------------------------------------

def test_http_plaintext_rejected_without_optout(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          api: {driver: "shal,http", address: "http://device.local"}
    """)
    with pytest.raises(shal.LoadError, match="insecure"):
        shal.load(p)


def test_http_plaintext_allowed_with_loud_optout(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          api:
            id: api
            driver: shal,http
            address: http://device.local
            insecure: true
    """)
    with shal.load(p) as hal:
        assert hal.get_node("api").driver is not None  # loads; no request made


# ---- tcp -------------------------------------------------------------------------

class _Echo(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            req = json.loads(line)
            self.wfile.write((json.dumps(
                {"echo": req["payload"], "addr": req["addr"]}) + "\n").encode())
            self.wfile.flush()


def test_tcp_exchange_roundtrip(tmp_path):
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), _Echo)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        p = write(tmp_path, f"""
            shal_version: 1
            root:
              net:
                id: net
                driver: shal,tcp
                address: 127.0.0.1:{port}
                insecure: true
        """)
        with shal.load(p) as hal:
            bus = hal.get_node("net").driver
            reply = bus.exchange("robot1", {"cmd": "ping"})
            assert reply == {"echo": {"cmd": "ping"}, "addr": "robot1"}
    finally:
        server.shutdown()
