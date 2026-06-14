"""Operating limits (issue #10): declared as JSON-Schema fragments on @op,
advertised verbatim in tool_schemas, enforced by the FRAMEWORK before any bus
I/O — a generated driver cannot forget or weaken the check."""
import pytest

import shal


@shal.register
class _LimitedPsu(shal.Driver):
    """Test double: records every accepted call so tests can prove the op body
    (the only path to bus I/O) never ran on a rejected call."""

    compatible = "test,limited-psu"
    kind = None
    llm_ready = True
    accepted: list = []  # class-level so tests can inspect across instances

    @shal.op("Set the output voltage (absolute setpoint).",
             unit="volt", side_effect="write",
             params={"volts": {"minimum": 0.0, "maximum": 32.0}})
    def set_voltage(self, volts: float) -> str:
        type(self).accepted.append(volts)
        return "ok"


PSU_YAML = ("shal_version: 1\n"
            "root:\n"
            "  psu: {id: psu, driver: 'test,limited-psu', address: 1}\n")


@pytest.fixture
def psu(tmp_path):
    _LimitedPsu.accepted.clear()
    p = tmp_path / "s.yaml"
    p.write_text(PSU_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        yield hal.get_device("psu")


# ---- slice 1: enforcement happens before the op body (= before any bus I/O) ----

def test_out_of_range_raises_limiterror_before_body(psu):
    with pytest.raises(shal.LimitError) as ei:
        psu.set_voltage(40.0)
    assert _LimitedPsu.accepted == []          # body never ran -> nothing sent
    assert ei.value.violations                  # structured, machine-readable
    v = ei.value.violations[0]
    assert v["param"] == "volts" and v["value"] == 40.0


def test_in_range_passes_through(psu):
    assert psu.set_voltage(3.3) == "ok"
    assert _LimitedPsu.accepted == [3.3]


# ---- slice 2: one artifact, two trust layers --------------------------------------

def test_limits_advertised_verbatim_in_tool_schemas(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(PSU_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        s = next(x for x in hal.tool_schemas() if x["name"] == "psu__set_voltage")
        volts = s["input_schema"]["properties"]["volts"]
        # the hint supplies the type; the declaration supplies the bounds —
        # the model sees EXACTLY what the framework enforces
        assert volts == {"type": "number", "minimum": 0.0, "maximum": 32.0}


def test_call_tool_returns_structured_refusal(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(PSU_YAML, encoding="utf-8")
    _LimitedPsu.accepted.clear()
    with shal.load(p) as hal:
        out = hal.call_tool("psu__set_voltage", {"volts": 40.0})
        assert out["ok"] is False
        assert out["rejected"] == "limits"          # machine-distinguishable from HopError
        assert "delivered" not in out               # nothing was sent — no ambiguity
        assert out["violations"][0]["param"] == "volts"
        assert _LimitedPsu.accepted == []


def test_bad_declaration_fails_at_decoration():
    with pytest.raises(ValueError, match="do not name parameters"):
        class _Bad(shal.Driver):  # noqa
            compatible = "test,bad-limit"

            @shal.op("x", params={"wattz": {"maximum": 1}})   # typo'd param name
            def set_power(self, watts: float) -> None: ...


# ---- slice 3a: op_limits() — per-instance narrowing (dp832-CH3 pattern) -----------

@shal.register
class _ChannelPsu(shal.Driver):
    """Family envelope 0-32 V, but channel 3 is the 5 V rail (address-dependent)."""

    compatible = "test,channel-psu"
    kind = None
    llm_ready = True
    accepted: list = []

    @shal.op("Set the output voltage.", unit="volt", side_effect="write",
             params={"volts": {"minimum": 0.0, "maximum": 32.0}})
    def set_voltage(self, volts: float) -> str:
        type(self).accepted.append(volts)
        return "ok"

    def op_limits(self):
        if self.addr == 3:
            return {"set_voltage": {"volts": {"maximum": 5.3}}}
        return {}


def _ch_yaml(addr: int) -> str:
    return ("shal_version: 1\n"
            "root:\n"
            f"  ch: {{id: ch, driver: 'test,channel-psu', address: {addr}}}\n")


def test_op_limits_narrows_per_instance(tmp_path):
    _ChannelPsu.accepted.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_ch_yaml(3), encoding="utf-8")
    with shal.load(p) as hal:
        dev = hal.get_device("ch")
        with pytest.raises(shal.LimitError):
            dev.set_voltage(12.0)                  # fine for CH1, not for CH3
        assert dev.set_voltage(5.0) == "ok"
        # the bound tool advertises the EFFECTIVE limit, not the family envelope
        s = next(x for x in hal.tool_schemas() if x["name"] == "ch__set_voltage")
        assert s["input_schema"]["properties"]["volts"]["maximum"] == 5.3


def test_op_limits_may_only_narrow(tmp_path):
    @shal.register
    class _Widener(shal.Driver):  # noqa
        compatible = "test,widening-psu"
        kind = None

        @shal.op("x", side_effect="write",
                 params={"volts": {"maximum": 32.0}})
        def set_voltage(self, volts: float) -> None: ...

        def op_limits(self):
            return {"set_voltage": {"volts": {"maximum": 99.0}}}   # WIDER -> refuse

    p = tmp_path / "s.yaml"
    p.write_text("shal_version: 1\nroot:\n"
                 "  x: {driver: 'test,widening-psu', address: 1}\n", encoding="utf-8")
    with pytest.raises(shal.LoadError, match="WIDEN"):
        shal.load(p)


# ---- slice 3b: YAML config.limits — installation policy, tighten-only -------------

def _rig_yaml(max_v: float) -> str:
    return ("shal_version: 1\n"
            "root:\n"
            "  psu:\n"
            "    id: psu\n"
            "    driver: test,limited-psu\n"
            "    address: 1\n"
            "    config:\n"
            "      limits:\n"
            "        set_voltage:\n"
            f"          volts: {{maximum: {max_v}}}\n")


def test_installation_limit_tightens_without_driver_edit(tmp_path):
    _LimitedPsu.accepted.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_rig_yaml(5.0), encoding="utf-8")      # rig feeds a 3.3 V board
    with shal.load(p) as hal:
        dev = hal.get_device("psu")
        with pytest.raises(shal.LimitError):
            dev.set_voltage(12.0)                        # device-legal, rig-illegal
        assert dev.set_voltage(3.3) == "ok"
        s = next(x for x in hal.tool_schemas() if x["name"] == "psu__set_voltage")
        assert s["input_schema"]["properties"]["volts"]["maximum"] == 5.0


def test_installation_limit_may_never_widen(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(_rig_yaml(40.0), encoding="utf-8")     # tries to exceed 32.0
    with pytest.raises(shal.LoadError, match="WIDEN"):
        shal.load(p)


# ---- slice 4: catalog surfacing + audit on rejection -------------------------------

def test_catalog_shows_declared_limits():
    entry = shal.catalog("test,limited-psu")
    op = next(o for o in entry["ops"] if o["name"] == "set_voltage")
    assert op["input_schema"]["properties"]["volts"]["maximum"] == 32.0


def test_rejected_write_is_audited(tmp_path):
    import logging
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    audit = logging.getLogger("shal.audit")
    handler = Collect(level=logging.INFO)
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    try:
        p = tmp_path / "s.yaml"
        p.write_text(PSU_YAML, encoding="utf-8")
        with shal.load(p) as hal:
            with pytest.raises(shal.LimitError):
                hal.get_device("psu").set_voltage(40.0)
        [rec] = [r for r in records if getattr(r, "op", "") == "set_voltage"]
        assert rec.outcome == "rejected"     # the attempt is on the record —
    finally:                                  # a safety review can see it was tried
        audit.removeHandler(handler)
        audit.setLevel(logging.NOTSET)
