"""Phase 1 test suite: load, lookup, txn, retry/idempotency policy, teardown."""
import textwrap
from pathlib import Path

import pytest

import shal
from shal.capabilities import TemperatureSensor

HERE = Path(__file__).parent
SETUP = HERE / "setup_sim.yaml"


def write(tmp_path, body: str) -> Path:
    p = tmp_path / "setup.yaml"
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ---- load + lookup -----------------------------------------------------------

def test_load_and_read():
    with shal.load(SETUP) as hal:
        dev = hal.get_device(id="ambient_temp")
        assert isinstance(dev, TemperatureSensor)
        assert dev.read_celsius() == pytest.approx(25.0, abs=0.07)


def test_lookup_forms_equivalent():
    with shal.load(SETUP) as hal:
        a = hal.get_device("ambient_temp")            # positional id
        b = hal.get_device(id="ambient_temp")
        c = hal.get_device("/bench/temp0")            # positional path
        d = hal.get_device(path="/bench/temp0")
        assert a is b is c is d


def test_sim_model_is_settable():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        bus.model_for(0x48).temp_c = 31.5
        assert hal.get_device("ambient_temp").read_celsius() == pytest.approx(31.5, abs=0.07)


def test_unknown_id_and_bad_args():
    with shal.load(SETUP) as hal:
        with pytest.raises(shal.LoadError):
            hal.get_device("nope")
        with pytest.raises(shal.LoadError):
            hal.get_device("x", id="y")


# ---- loader invariants ---------------------------------------------------------

def test_schema_rejects_unknown_key(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          a: {driver: "shal,sim-i2c", address: sim0, capability: temp}
    """)
    with pytest.raises(shal.LoadError, match="schema"):
        shal.load(p)


def test_missing_version_rejected(tmp_path):
    p = write(tmp_path, "root: {a: {driver: 'shal,sim-i2c', address: sim0}}")
    with pytest.raises(shal.LoadError):
        shal.load(p)


def test_duplicate_id_fails_loudly(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          a:
            id: dup
            driver: shal,sim-i2c
            address: sim0
          b:
            id: dup
            driver: shal,sim-i2c
            address: sim1
    """)
    with pytest.raises(shal.LoadError, match="duplicate id"):
        shal.load(p)


def test_unknown_compatible_fails_load(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          a: {driver: "acme,unobtainium", address: 1}
    """)
    with pytest.raises(shal.LoadError, match="unobtainium"):
        shal.load(p)


def test_address_grammar_validated_at_load(tmp_path):
    p = write(tmp_path, """
        shal_version: 1
        root:
          bench:
            driver: shal,sim-i2c
            address: sim0
            children:
              t: {driver: "ti,tmp102", address: 0x99}
    """)
    with pytest.raises(shal.LoadError, match="0x03-0x77"):
        shal.load(p)


def test_missing_env_var_names_the_name(tmp_path, monkeypatch):
    monkeypatch.delenv("SHAL_NOPE", raising=False)
    p = write(tmp_path, """
        shal_version: 1
        root:
          a: {driver: "shal,sim-i2c", address: "${SHAL_NOPE}"}
    """)
    with pytest.raises(shal.LoadError, match="SHAL_NOPE"):
        shal.load(p)


def test_config_only_node_names_the_rule(tmp_path):
    # #95: a node with `config:` but no address/routes/to (the natural first
    # draft for a cloud device) used to fail with jsonschema's generic
    # "not valid under any of the given schemas" — naming neither the node nor
    # the rule. It must now name both.
    p = write(tmp_path, """
        shal_version: 1
        root:
          vacuum:
            id: vac
            driver: "ecovacs,deebot"
            config: {account: someone@example.com}
    """)
    with pytest.raises(shal.LoadError,
                        match=r"root/vacuum.*needs exactly one of address \| routes \| to"):
        shal.load(p)


def test_node_with_address_and_other_error_keeps_generic_message(tmp_path):
    # A node that HAS an address but fails for an unrelated reason must not be
    # told it "needs exactly one of address | routes | to" — that would send
    # the author looking for the wrong problem.
    p = write(tmp_path, """
        shal_version: 1
        root:
          vacuum:
            id: vac
            driver: "ecovacs,deebot"
            address: "1.2.3.4"
            bogus_key: oops
    """)
    with pytest.raises(shal.LoadError, match="schema violation") as excinfo:
        shal.load(p)
    assert "needs exactly one of" not in str(excinfo.value)


def test_address_and_to_together_keeps_generic_message(tmp_path):
    # A node with BOTH address and to trips the same oneOf as a node with
    # neither — but it's a different mistake and must not get the same
    # friendly rewrite. (address + routes is a routed node, #231.)
    p = write(tmp_path, """
        shal_version: 1
        root:
          vacuum:
            id: vac
            driver: "ecovacs,deebot"
            address: "1.2.3.4"
            to: $other
    """)
    with pytest.raises(shal.LoadError, match="schema violation") as excinfo:
        shal.load(p)
    assert "needs exactly one of" not in str(excinfo.value)


def test_routes_without_address_names_the_key(tmp_path):
    # #231: the main route's address is `address:`; routes alone stays invalid
    p = write(tmp_path, """
        shal_version: 1
        root:
          a:
            driver: "ti,tmp102"
            routes:
              - {via: /x, address: 0x48}
    """)
    with pytest.raises(shal.LoadError, match="root/a' has routes but no address"):
        shal.load(p)


# ---- retry & idempotency (locked decision 6) -----------------------------------

def test_idempotent_read_retries_once():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_next = 1  # transient drop, delivered=no
        assert dev.read_celsius() == pytest.approx(25.0, abs=0.07)  # magic retry


def test_two_drops_exhaust_single_retry():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_next = 2  # reconnect once, retry once - not a loop
        with pytest.raises(shal.HopError):
            dev.read_celsius()


def test_delivered_unknown_never_refired():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        bus.fail_delivered_unknown = True
        with pytest.raises(shal.HopError) as ei:
            dev.read_celsius()  # even idempotent ops: delivery unknown -> raise
        assert ei.value.delivered == "unknown"


# ---- caching + lifecycle ---------------------------------------------------------

def test_connection_cached_across_reads():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        dev = hal.get_device("ambient_temp")
        dev.read_celsius()
        dev.read_celsius()
        assert bus.connect_count == 1  # 1 connect for 2 reads


def test_teardown_closes_buses():
    hal = shal.load(SETUP)
    bus = hal.get_node("bench").driver
    hal.get_device("ambient_temp").read_celsius()
    assert bus.is_active()
    hal.close()
    assert not bus.is_active()


def test_kinds_introspection_honest():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        assert shal.ByteTransport in bus.kinds()
        assert shal.CommandTransport not in bus.kinds()
