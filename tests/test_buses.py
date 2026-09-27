"""Bus suite: mux (incl. the v1 two-mux regression), local, ssh argv,
i2c-cli rendering, http TLS rule, tcp roundtrip."""
import json
import logging
import socketserver
import stat
import sys
import textwrap
import threading
import urllib.error
from pathlib import Path

import pytest

import shal
from shal.buses import http_bus
from shal.buses.i2c_cli import I2cCliBus, parse_output, render_ops
from shal.buses.scpi_raw import ScpiRawBus
from shal.buses.sim import SimI2cBus
from shal.buses.ssh import ssh_argv
from shal.log import redact_url
from shal.node import Node
from shal.transport import CommandTransport, Completed, Read, Transport, Write

# nxp,pca9548 is a vendor part, so the chip and its sim model live in examples/
# (#149, ADK §3.6). The mechanism these mux tests pin — MuxState, MuxChannel —
# is core. Imported from there so the chip is tested where it now lives.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "examples/drivers/pca9548"))
import pca9548_driver  # noqa: E402,F401  (registers nxp,pca9548)
import pca9548_sim  # noqa: E402,F401     (registers its sim model)


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


def test_i2c_cli_empty_stdout_raises_hoperror():
    """D12 (issue #108): exit 0 + empty stdout must raise HopError, not
    silently return b"" — a downstream driver (e.g. tmp102) indexing that
    empty result used to die with a bare IndexError instead."""
    class _FakeLocal(Transport, CommandTransport):
        def run(self, argv, stdin=b"") -> Completed:
            return Completed(stdout=b"", stderr=b"", exit=0)

    root = Node("here")
    root.driver = _FakeLocal(root)
    child = Node("i2c0", address="/dev/i2c-1", parent=root)
    bus = I2cCliBus(child)
    child.driver = bus
    with pytest.raises(shal.HopError, match="short read") as exc_info:
        bus.txn(0x48, [Write(b"\x00"), Read(2)])
    assert exc_info.value.delivered == "unknown"


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


def test_i2c_cli_bad_device_path_redacts_credentials(tmp_path):
    # issue #126: i2c-cli's own bus address is ${ENV}-resolved just like
    # tcp/scpi-raw's host:port — a misplaced creds URL must not echo verbatim
    p = write(tmp_path, """
        shal_version: 1
        root:
          here:
            driver: shal,local
            address: localhost
            children:
              i2c0:
                driver: "shal,i2c-cli"
                address: "https://user:secret@device.local/x?token=abc"
    """)
    with pytest.raises(shal.LoadError, match="/dev/i2c") as ei:
        shal.load(p)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc" not in msg
    assert "device.local" in msg


def test_i2c_cli_child_address_error_redacts_credentials():
    # issue #126: the child address is ${ENV}-resolved just like the bus's
    # own — loader.py resolves every node's address the same way, so a
    # misplaced creds URL landing here must not echo verbatim
    root = Node("here")
    child = Node("i2c0", address="/dev/i2c-1", parent=root)
    bus = I2cCliBus(child)
    with pytest.raises(shal.LoadError, match="0x03-0x77") as ei:
        bus.validate_address("https://user:secret@evil.example/x?token=abc123")
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_i2c_cli_child_address_error_keeps_plain_value():
    # a non-credential bad value (a topology typo) must still be echoed in
    # full — redaction must not over-mask ordinary debugging info
    root = Node("here")
    child = Node("i2c0", address="/dev/i2c-1", parent=root)
    bus = I2cCliBus(child)
    with pytest.raises(shal.LoadError, match="0x03-0x77") as ei:
        bus.validate_address("ch9")
    assert "ch9" in str(ei.value)


def test_spi_cli_bad_device_path_redacts_credentials(tmp_path):
    # issue #126: spi-cli's own bus address is ${ENV}-resolved just like
    # tcp/scpi-raw's host:port — a misplaced creds URL must not echo verbatim
    p = write(tmp_path, """
        shal_version: 1
        root:
          here:
            driver: shal,local
            address: localhost
            children:
              spi0:
                driver: "shal,spi-cli"
                address: "https://user:secret@device.local/x?token=abc"
    """)
    with pytest.raises(shal.LoadError, match="spidev") as ei:
        shal.load(p)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc" not in msg
    assert "device.local" in msg


def test_mux_bad_channel_redacts_credentials(tmp_path):
    # issue #126: a mux channel address is ${ENV}-resolved like any other —
    # a misplaced creds URL must not echo verbatim
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
                  ch0:
                    address: "https://user:secret@evil.example/x?token=abc123"
                    children:
                      d: {driver: "ti,tmp102", address: 0x48}
    """)
    with pytest.raises(shal.LoadError, match="pca9548 channel") as ei:
        shal.load(p)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_mux_bad_channel_keeps_plain_value(tmp_path):
    # a non-credential bad value (a topology typo) must still be echoed in
    # full — redaction must not over-mask ordinary debugging info
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
                  ch0:
                    address: "ch9"
                    children:
                      d: {driver: "ti,tmp102", address: 0x48}
    """)
    with pytest.raises(shal.LoadError, match="pca9548 channel") as ei:
        shal.load(p)
    assert "ch9" in str(ei.value)


def test_sim_i2c_child_address_error_redacts_credentials():
    # issue #126: the child address is ${ENV}-resolved (the sim-i2c twin of
    # i2c-cli's own check) — a misplaced creds URL must not echo verbatim
    node = Node("bench", address="sim0")
    bus = SimI2cBus(node)
    with pytest.raises(shal.LoadError, match="0x03-0x77") as ei:
        bus.validate_address("https://user:secret@evil.example/x?token=abc123")
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_sim_i2c_child_address_error_keeps_plain_value():
    # a non-credential bad value (a topology typo) must still be echoed in
    # full — redaction must not over-mask ordinary debugging info
    node = Node("bench", address="sim0")
    bus = SimI2cBus(node)
    with pytest.raises(shal.LoadError, match="0x03-0x77") as ei:
        bus.validate_address("ch9")
    assert "ch9" in str(ei.value)


class _Opaque:
    """Stand-in for a resolved address reaching a str/int-only grammar check
    as some other type — its ``str()`` is what a caller might see leak."""

    def __init__(self, s: str) -> None:
        self._s = s

    def __str__(self) -> str:
        return self._s


def test_scpi_raw_child_address_error_redacts_credentials():
    # issue #126: the label grammar check only rejects on TYPE, so any
    # non-str/int value reaching it (however it got there) must still have
    # its stringified content redacted before it is echoed
    node = Node("scpi0", address="10.0.0.5:5025")
    node.spec = {"insecure": True}
    bus = ScpiRawBus(node)
    with pytest.raises(shal.LoadError, match="instrument/channel label"):
        bus.validate_address("")
    bad = _Opaque("https://user:secret@evil.example/x?token=abc123")
    with pytest.raises(shal.LoadError, match="instrument/channel label") as ei:
        bus.validate_address(bad)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc123" not in msg
    assert "evil.example" in msg


def test_scpi_raw_child_address_error_keeps_plain_value():
    # a non-credential bad value must still be echoed in full — redaction
    # must not over-mask ordinary debugging info
    node = Node("scpi0", address="10.0.0.5:5025")
    node.spec = {"insecure": True}
    bus = ScpiRawBus(node)
    with pytest.raises(shal.LoadError, match="instrument/channel label") as ei:
        bus.validate_address(["not-a-label"])
    assert "not-a-label" in str(ei.value)


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


# ---- secret redaction (issue #20): credentials never reach logs/errors -----------

@pytest.mark.parametrize("raw, expected", [
    ("https://user:secret@host.local/api/v2?token=abc", "https://host.local/api/v2"),
    ("https://host.local:8443/x", "https://host.local:8443/x"),
    ("user:secret@10.0.0.5:5025", "10.0.0.5:5025"),
    ("10.0.0.5:5025", "10.0.0.5:5025"),
])
def test_redact_url_strips_credentials(raw, expected):
    out = redact_url(raw)
    assert out == expected
    assert "secret" not in out and "token" not in out


def test_http_load_error_redacts_credentials_in_address(tmp_path):
    # issue #101: a malformed creds-URL address (e.g. from ${ENV}) must not
    # echo userinfo verbatim in the load-time LoadError text
    p = write(tmp_path, """
        shal_version: 1
        root:
          api: {driver: "shal,http", address: "htps://user:secret@device.local/api?token=abc"}
    """)
    with pytest.raises(shal.LoadError, match="http\\(s\\)") as ei:
        shal.load(p)
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc" not in msg  # creds stripped
    assert "device.local" in msg                            # endpoint kept (debuggable)


@pytest.mark.parametrize("driver", ["shal,tcp", "shal,scpi-raw"])
def test_hostport_load_error_redacts_misplaced_creds_url(tmp_path, driver):
    # issue #101 audit: host:port buses — a creds URL misplaced into the
    # address fails port parsing; the echoed address must be redacted too
    p = write(tmp_path, f"""
        shal_version: 1
        root:
          net: {{driver: "{driver}", address: "https://user:secret@10.0.0.5"}}
    """)
    with pytest.raises(shal.LoadError, match="host:port") as ei:
        shal.load(p)
    msg = str(ei.value)
    assert "secret" not in msg
    assert "10.0.0.5" in msg


def test_http_load_error_clean_address_not_masked(tmp_path):
    # no false masking: a credential-free malformed address echoes verbatim
    p = write(tmp_path, """
        shal_version: 1
        root:
          api: {driver: "shal,http", address: "device.local"}
    """)
    with pytest.raises(shal.LoadError, match="got 'device.local'"):
        shal.load(p)


def _capture_shal_debug(fn):
    """Run `fn` with a capturing handler on the 'shal' logger at DEBUG, then
    detach it. The library never configures logging — the app (here, the test)
    attaches and removes its own handler."""
    records: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Capture(level=logging.DEBUG)
    log = logging.getLogger("shal")
    prev = log.level
    log.addHandler(handler)
    log.setLevel(logging.DEBUG)
    try:
        fn()
    finally:
        log.removeHandler(handler)
        log.setLevel(prev)
    return records


def test_bind_log_redacts_credentials_in_address(tmp_path):
    # issue #117: the happy path — a *valid* creds-bearing address binds fine,
    # and the DEBUG bind record must not carry the password anywhere
    p = write(tmp_path, """
        shal_version: 1
        root:
          api: {driver: "shal,http", address: "https://user:secret@device.local/v1?token=abc"}
    """)
    records = _capture_shal_debug(lambda: shal.load(p).close())
    binds = [r for r in records if getattr(r, "event", "") == "bind"]
    assert binds, "no bind record emitted"
    for r in records:
        # the `addr` field *and* the formatted message — a future change that
        # moves the address into the message text must still fail this test
        assert "secret" not in str(getattr(r, "addr", ""))
        assert "token=abc" not in str(getattr(r, "addr", ""))
        assert "secret" not in r.getMessage() and "token=abc" not in r.getMessage()
    assert binds[0].addr == "https://device.local/v1"  # endpoint kept, creds gone


@pytest.mark.parametrize("address, secret, expected", [
    ("http://user:s3cret@host/", "s3cret", "http://host/"),               # userinfo
    ("https://host/v1?token=t0kVAL9x", "t0kVAL9x", "https://host/v1"),   # query token
])
def test_bind_log_never_carries_secret_caplog(tmp_path, caplog, address, secret, expected):
    # #142 (test half of #117): the "secrets never in logs" non-negotiable
    # (docs/agents/context.md). Load under pytest's caplog at DEBUG and assert the
    # secret is in no record at any level — message, args, or any extra field.
    # `insecure: true` only so the plaintext http:// case is allowed to bind.
    p = write(tmp_path, f"""
        shal_version: 1
        root:
          api: {{driver: "shal,http", address: "{address}", insecure: true}}
    """)
    caplog.set_level(logging.DEBUG, logger="shal")
    # shal.audit has propagate=False, so the root-level caplog handler would miss
    # it — attach the handler there too, so "every record" really means every one
    audit = logging.getLogger("shal.audit")
    audit.addHandler(caplog.handler)
    try:
        shal.load(p).close()
    finally:
        audit.removeHandler(caplog.handler)
    binds = [r for r in caplog.records
             if r.name == "shal.loader" and getattr(r, "event", "") == "bind"]
    assert binds, "no shal.loader bind record captured — test would pass vacuously"
    for r in caplog.records:
        assert secret not in r.getMessage()
        assert secret not in str(r.msg) and secret not in repr(r.args)
        for key, value in vars(r).items():
            assert secret not in repr(value), f"{secret!r} leaked via {r.name}.{key}"
    assert secret not in caplog.text
    assert binds[0].addr == expected  # endpoint kept, secret gone


@pytest.mark.parametrize("address, expected", [
    ("https://device.local/v1", "https://device.local/v1"),   # clean URL
    ("sim0", "sim0"),                                          # opaque label
])
def test_bind_log_keeps_clean_address_unredacted(tmp_path, address, expected):
    # no false masking: over-redaction destroys the log's operational value
    driver = "shal,http" if "://" in address else "shal,sim-i2c"
    p = write(tmp_path, f"""
        shal_version: 1
        root:
          dev: {{driver: "{driver}", address: "{address}"}}
    """)
    records = _capture_shal_debug(lambda: shal.load(p).close())
    binds = [r for r in records if getattr(r, "event", "") == "bind"]
    assert [r.addr for r in binds] == [expected]


def test_http_error_redacts_credentials_in_url(tmp_path, monkeypatch):
    # a credential-bearing ${ENV}-style URL must not surface in the HopError text
    p = write(tmp_path, """
        shal_version: 1
        root:
          api:
            id: api
            driver: shal,http
            address: https://user:secret@device.local/api/v2?token=abc
    """)
    with shal.load(p) as hal:
        bus = hal.get_node("api").driver

        def boom(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, None)

        monkeypatch.setattr(http_bus.urllib.request, "urlopen", boom)
        with pytest.raises(shal.HopError) as ei:
            bus.exchange("cmd", {"x": 1})
    msg = str(ei.value)
    assert "secret" not in msg and "token=abc" not in msg  # creds stripped
    assert "device.local" in msg                            # endpoint kept (debuggable)
