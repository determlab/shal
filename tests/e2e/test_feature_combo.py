"""End-to-end capstone: a reusable board template `use:`d twice with different
params, driven over the real-subprocess CLI stack, all inside the JSON-lines
flight recorder with the audit channel on. Ties includes + with: params +
config: + multi-device dispatch + structured logging + audit + the LLM tool
surface into one run.

Driver + tool + fixtures live in conftest.py.
"""
import json

import pytest

import shal


def test_two_includes_drive_independently_with_flight_recorder(two_board_rig):
    setup, tmp_path = two_board_rig
    flight = tmp_path / "flight.jsonl"

    with shal.load(setup) as hal:
        # both boards came from ONE template, parameterized to distinct ids
        assert hal.get_node("oven_temp").path == "/rack_a/thermo"
        assert hal.get_node("chamber_temp").path == "/rack_b/thermo"

        with shal.logging.capture(flight):
            assert hal.get_device("oven_temp").read_celsius() == pytest.approx(25.0)
            assert hal.get_device("chamber_temp").read_celsius() == pytest.approx(25.0)
            assert hal.get_device("oven_temp").set_threshold("80") == "ok"

    records = [json.loads(line) for line in flight.read_text(encoding="utf-8").splitlines()]

    # the run subprocesses really happened, captured at DEBUG with correlated txns
    runs = [r for r in records if r.get("event") == "run"]
    assert runs and all("txn" in r for r in runs)

    # the WRITE was audited; the READS were not (audit covers state changes only)
    audits = [r for r in records if r.get("event") == "audit"]
    assert [a["op"] for a in audits] == ["set_threshold"]
    assert audits[0]["outcome"] == "ok"


def test_llm_tool_surface_over_the_combo(two_board_rig):
    setup, _ = two_board_rig
    with shal.load(setup) as hal:
        names = {s["name"] for s in hal.tool_schemas()}
        assert {"oven_temp__read_celsius", "chamber_temp__set_threshold"} <= names

        # side-effect gating distinguishes the read from the write
        cat = {c["name"]: c["side_effect"] for c in hal.tool_catalog()}
        assert cat["oven_temp__read_celsius"] == "none"
        assert cat["oven_temp__set_threshold"] == "write"

        # dispatch a real call through the generated tool name
        out = hal.call_tool("chamber_temp__read_celsius")
        assert out["ok"] and out["result"] == pytest.approx(25.0)
