"""A sim-bus child with no registered sim model: the error names the missing
decorator and the compatible; model_for raises a LookupError listing what has one."""
import textwrap

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model
from shal.errors import HopError
from shal.transport import Read, Write

YAML = """
    shal_version: 1
    root:
      bench:
        id: bench
        driver: {bus}
        address: sim0
        children:
          ok: {{id: ok, driver: "{ok}", address: {ok_addr}}}
          dev: {{id: dev, driver: "{comp}", address: {addr}}}
"""


def _bare(compatible, kind):
    @shal.idempotent
    @shal.op("Read nothing.", side_effect="none")
    def read_nothing(self) -> int:
        return 0

    return shal.register(type("Bare", (shal.Driver,), {
        "compatible": compatible, "kind": kind, "llm_ready": True,
        "read_nothing": read_nothing}), override=True)


@msg_sim_model("test,msg-ok")
class _OkModel:
    def handle(self, msg):
        return {}


# bus, modeled sibling (compat, addr), unmodeled (compat, addr), kind, decorator
CASES = [
    ("shal,sim-i2c", "shal,sim-sensor", "0x48", "test,bare-i2c", "0x49",
     shal.ByteTransport, "@sim_model('"),
    ("shal,sim-scpi", "shal,sim-psu", "psu0", "test,bare-scpi", "bare0",
     shal.MessageTransport, "@scpi_sim_model('"),
    ("shal,sim-msg", "test,msg-ok", "ok0", "test,bare-msg", "bare0",
     shal.MessageTransport, "@msg_sim_model('"),
]


@pytest.fixture(params=CASES, ids=["i2c", "scpi", "msg"])
def case(request, tmp_path):
    bus, ok, ok_addr, comp, addr, kind, deco = request.param
    _bare(comp, kind)
    if ok == "test,msg-ok":
        _bare(ok, kind)
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(YAML.format(
        bus=bus, ok=ok, ok_addr=ok_addr, comp=comp, addr=addr)), encoding="utf-8")
    with shal.load(p) as hal:
        b = hal.get_node("bench").driver
        # i2c addresses are ints; scpi/msg are labels
        yield b, comp, (int(addr, 16) if bus == "shal,sim-i2c" else addr), deco


def _txn(bus, addr):
    if isinstance(addr, int):
        return bus.txn(addr, [Write(b"\x00"), Read(1)])
    return bus.exchange(addr, {"scpi": "X?", "query": True})


def test_transaction_miss_names_decorator_and_compatible(case):
    bus, comp, addr, deco = case
    with pytest.raises(HopError) as ei:
        _txn(bus, addr)
    msg = str(ei.value)
    assert deco in msg
    assert comp in msg


def test_model_for_miss_is_lookup_error_listing_addresses(case):
    bus, comp, addr, _ = case
    with pytest.raises(LookupError) as ei:
        bus.model_for(addr)
    msg = str(ei.value)
    assert "addresses with models" in msg
    assert "none" not in msg   # the modeled sibling is listed


# scpi/msg addresses are ${ENV}-resolved labels and can carry a token in a URL;
# model_for's "addresses with models" list must redact them, same as the miss
# address right next to them (#291 review)
MODELLED_URL_CASES = [
    ("shal,sim-scpi", "shal,sim-psu"),
    ("shal,sim-msg", "test,msg-ok"),
]


@pytest.fixture(params=MODELLED_URL_CASES, ids=["scpi", "msg"])
def url_case(request, tmp_path):
    bus, ok = request.param
    p = tmp_path / "s.yaml"
    p.write_text(textwrap.dedent(f"""
        shal_version: 1
        root:
          bench:
            id: bench
            driver: {bus}
            address: sim0
            children:
              ok: {{id: ok, driver: "{ok}", address: "http://user:SECRET@host/x"}}
    """), encoding="utf-8")
    with shal.load(p) as hal:
        yield hal.get_node("bench").driver


def test_model_for_miss_redacts_modelled_addresses(url_case):
    bus = url_case
    with pytest.raises(LookupError) as ei:
        bus.model_for("missing")
    msg = str(ei.value)
    assert "SECRET" not in msg
    assert "host" in msg
