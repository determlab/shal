"""LLM tool surface — metadata, bind-time enforcement, schema gen, dispatch."""
from pathlib import Path

import pytest

import shal

SETUP = Path(__file__).parent / "setup_sim.yaml"


# ---- bind-time enforcement -----------------------------------------------------

def test_llm_ready_requires_op_metadata(tmp_path):
    @shal.register
    class Bad(shal.Driver):
        compatible = "test,bad-llm"
        kind = None
        llm_ready = True

        def do_thing(self) -> int:          # no @shal.op -> must fail at bind
            return 1

    p = tmp_path / "s.yaml"
    p.write_text("shal_version: 1\nroot:\n  x: {driver: 'test,bad-llm', address: 1}\n",
                 encoding="utf-8")
    with pytest.raises(shal.LoadError, match="missing @shal.op"):
        shal.load(p)


# ---- schema generation ---------------------------------------------------------

def test_tool_schemas_anthropic_shape():
    with shal.load(SETUP) as hal:
        schemas = {s["name"]: s for s in hal.tool_schemas()}
        s = schemas["ambient_temp__read_celsius"]
        assert set(s) == {"name", "description", "input_schema"}
        assert s["input_schema"] == {"type": "object", "properties": {},
                                     "additionalProperties": False}
        assert "celsius" in s["description"].lower()
        assert "idempotent" in s["description"].lower()


def test_tool_catalog_reports_side_effects():
    with shal.load(SETUP) as hal:
        cat = {c["name"]: c for c in hal.tool_catalog()}
        c = cat["ambient_temp__read_celsius"]
        assert c["side_effect"] == "none" and c["idempotent"] is True


def test_param_schema_from_type_hints(tmp_path):
    @shal.register
    class Knob(shal.Driver):
        compatible = "test,knob"
        kind = None

        @shal.op("Set the level. Call to change the device level.", side_effect="write")
        def set_level(self, level: int, fine: bool = False) -> str:
            return f"{level}/{fine}"

    p = tmp_path / "s.yaml"
    p.write_text("shal_version: 1\nroot:\n  k: {id: k, driver: 'test,knob', address: 1}\n",
                 encoding="utf-8")
    with shal.load(p) as hal:
        schema = next(s for s in hal.tool_schemas() if s["name"] == "k__set_level")
        props = schema["input_schema"]["properties"]
        assert props["level"] == {"type": "integer"}
        assert props["fine"] == {"type": "boolean"}
        assert schema["input_schema"]["required"] == ["level"]  # `fine` has a default


# ---- dispatch ------------------------------------------------------------------

def test_call_tool_dispatches_and_reads():
    with shal.load(SETUP) as hal:
        hal.get_node("bench").driver.model_for(0x48).temp_c = 20.0
        out = hal.call_tool("ambient_temp__read_celsius")
        assert out["ok"] is True
        assert out["result"] == pytest.approx(20.0, abs=0.07)


def test_call_tool_reports_delivery_unknown_not_retried():
    with shal.load(SETUP) as hal:
        bus = hal.get_node("bench").driver
        hal.get_device("ambient_temp").read_celsius()
        bus.fail_delivered_unknown = True
        out = hal.call_tool("ambient_temp__read_celsius")
        assert out["ok"] is False and out["delivered"] == "unknown"


def test_call_tool_unknown_name():
    with shal.load(SETUP) as hal:
        with pytest.raises(shal.LoadError, match="no tool"):
            hal.call_tool("ghost__do")
