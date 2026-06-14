"""Stage-2 harness: the GENERATED ecovacs,cloud-n20 bus + the stage-1
GENERATED ecovacs,deebot-n20 driver, validated end-to-end over real HTTP
against the harness fake portal (an independent implementation of
docs/deebot-cloud-transport.md).

Run from the repo root:  python -m pytest examples/driver-creator/deebot/harness -q
Skips cleanly until generated/bus.py exists.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CASE = HERE.parent
GEN = CASE / "generated"

pytestmark = pytest.mark.skipif(not (GEN / "bus.py").exists(),
                                reason="not generated yet")

if (GEN / "bus.py").exists():
    for p in (str(GEN), str(HERE)):
        if p not in sys.path:
            sys.path.insert(0, p)

    import shal
    import bus      # noqa: F401  -- registers the generated ecovacs,cloud-n20
    import driver   # noqa: F401  -- registers the generated ecovacs,deebot-n20
    from fake_portal import FakePortal

TOPO = HERE / "topology_stage2.yaml"


@pytest.fixture()
def portal():
    with FakePortal() as p:
        yield p


@pytest.fixture()
def hal(portal, monkeypatch):
    # credentials + portal override per docs/deebot-cloud-transport.md §9
    monkeypatch.setenv("ECOVACS_EMAIL", "bench@example.com")
    monkeypatch.setenv("ECOVACS_PASSWORD", "hunter2")
    monkeypatch.setenv("ECOVACS_PORTAL_URL", portal.base_url)
    with shal.load(str(TOPO)) as h:
        yield h


@pytest.fixture()
def dut(hal):
    return hal.get_device("dut")


def test_reads_match_bench_defaults(dut, portal):
    assert dut.get_battery_percent() == 87
    assert dut.get_clean_state() == "idle"
    portal.robot.battery = 9
    assert dut.get_battery_percent() == 9


def test_clean_cycle_over_http(dut, portal):
    dut.start_cleaning()
    assert portal.robot.state == "clean" and portal.robot.docked is False
    assert dut.get_clean_state() == "clean"
    dut.pause()
    assert dut.get_clean_state() == "pause"
    dut.resume()
    assert dut.get_clean_state() == "clean"
    dut.stop_cleaning()
    assert dut.get_clean_state() == "idle"


def test_dock_from_undocked_goes_charging(dut, portal):
    dut.start_cleaning()
    dut.dock()
    assert portal.robot.state == "goCharging" and portal.robot.docked is True
    assert dut.get_clean_state() == "goCharging"


def test_dock_when_already_docked_succeeds(dut, portal):
    assert portal.robot.docked is True
    before = portal.robot.state
    dut.dock()                      # 30007 path: must not raise
    assert portal.robot.state == before and portal.robot.docked is True


def test_locate_plays_sound_without_state_change(dut, portal):
    before = (portal.robot.state, portal.robot.docked)
    dut.locate()
    assert (portal.robot.state, portal.robot.docked) == before


def test_tool_catalog_marks_actuations_writable(hal):
    cat = {t["op"]: t for t in hal.tool_catalog() if t["device"] == "dut"}
    for op_name in ("start_cleaning", "dock"):
        assert cat[op_name]["annotations"]["readOnlyHint"] is False
    for op_name in ("get_battery_percent", "get_clean_state"):
        assert cat[op_name]["annotations"]["readOnlyHint"] is True
