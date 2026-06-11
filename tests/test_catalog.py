"""shal.catalog() — the authoring surface for constructing topologies (issue #1)."""
import pytest

import shal


def test_catalog_lists_buses_and_drivers():
    cat = shal.catalog()
    assert cat["schema_version"] == "1.0"
    assert "ti,tmp102" in {d["compatible"] for d in cat["drivers"]}
    bus_comps = {b["compatible"] for b in cat["buses"]}
    assert {"shal,i2c-cli", "shal,sim-i2c"} <= bus_comps


def test_catalog_driver_detail_is_derived_plus_authored():
    d = shal.catalog("ti,tmp102")
    assert d["role"] == "driver"
    assert d["capability"] == "TemperatureSensor"        # derived from the Protocol
    assert d["requires_parent_kind"] == "ByteTransport"  # derived from `kind`
    assert d["address_schema"]["examples"] == [72]       # authored via authoring_meta
    op = next(o for o in d["ops"] if o["name"] == "read_celsius")
    assert op["unit"] == "celsius"
    assert op["annotations"] == {"readOnlyHint": True, "idempotentHint": True,
                                 "destructiveHint": False}


def test_catalog_bus_detail_kinds_and_child_schema():
    b = shal.catalog("shal,i2c-cli")
    assert b["role"] == "bus"
    assert b["requires_parent_kind"] == "CommandTransport"   # renders onto argv
    assert "ByteTransport" in b["provides_kinds"]
    assert b["child_address_schema"]["examples"] == [72]


def test_catalog_compact_omits_detail():
    cat = shal.catalog()
    entry = next(d for d in cat["drivers"] if d["compatible"] == "ti,tmp102")
    assert "ops" not in entry and "address_schema" not in entry  # progressive disclosure


def test_catalog_unknown_compatible_raises():
    with pytest.raises(shal.LoadError):
        shal.catalog("nope,nope")
