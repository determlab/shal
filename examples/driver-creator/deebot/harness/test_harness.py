"""Stage-1 harness: validates a GENERATED ecovacs,deebot-n20 driver against
the GOLDEN playground sim cloud + the docs' worked examples (DN20-PROTO §7).

Run from the repo root:  python -m pytest examples/driver-creator/deebot/harness -q
Skips cleanly until generated/driver.py exists.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
CASE = HERE.parent                      # examples/driver-creator/deebot
GEN = CASE / "generated"
REPO = CASE.parents[2]
PLAYGROUND = REPO / "examples" / "demos" / "deebot"   # the golden reference sim

pytestmark = pytest.mark.skipif(not (GEN / "driver.py").exists(),
                                reason="not generated yet")

if (GEN / "driver.py").exists():
    for p in (str(GEN), str(HERE), str(PLAYGROUND)):
        if p not in sys.path:
            sys.path.insert(0, p)

    import shal
    import sim_cloud  # noqa: F401  -- registers the golden playground,sim-cloud bus
    import driver     # noqa: F401  -- registers the generated ecovacs,deebot-n20
    try:
        import sim    # noqa: F401  -- the generated sim model, if importable
    except Exception:
        pass
    import sim_harness
    from shal.buses import sim_msg
    # Force the harness model to win for any sim-msg-based validation: even if
    # the generated sim.py registered its own model for this compatible, the
    # independent harness model is the one that counts.
    sim_msg.MSG_SIM_MODELS["ecovacs,deebot-n20"] = sim_harness.DeebotN20Model

TOPO = HERE / "topology.yaml"


@pytest.fixture()
def hal():
    with shal.load(str(TOPO)) as h:
        yield h


@pytest.fixture()
def dut(hal):
    return hal.get_device("dut")


@pytest.fixture()
def model(hal):
    """The golden sim's robot state machine — set/inspect device state."""
    return hal.get_device("cloud").model_for("bot1")


# -- worked examples (DN20-PROTO §7) ------------------------------------------

def test_e1_battery_reads_bench_default(dut):
    assert dut.get_battery_percent() == 87


def test_e1_battery_tracks_device_state(dut, model):
    model.battery = 9
    assert dut.get_battery_percent() == 9


def test_e2_state_reads_idle_at_power_on(dut):
    assert dut.get_clean_state() == "idle"


def test_e3_clean_cycle_start_pause_resume_stop(dut, model):
    dut.start_cleaning()
    assert model.state == "clean" and model.docked is False
    assert dut.get_clean_state() == "clean"
    dut.pause()
    assert dut.get_clean_state() == "pause"
    dut.resume()
    assert dut.get_clean_state() == "clean"
    dut.stop_cleaning()
    assert dut.get_clean_state() == "idle"


def test_e4_dock_from_undocked_goes_charging(dut, model):
    dut.start_cleaning()
    assert model.docked is False
    dut.dock()
    assert model.state == "goCharging" and model.docked is True
    assert dut.get_clean_state() == "goCharging"


def test_e4_dock_when_already_docked_succeeds(dut, model):
    # power-on default is docked: the robot answers 30007 ("already
    # charging") and a dock request MUST treat that as success (docs §2).
    assert model.docked is True
    before = model.state
    dut.dock()                      # must not raise
    assert model.state == before and model.docked is True


def test_e5_locate_plays_sound_without_state_change(dut, model):
    before = (model.state, model.docked)
    dut.locate()                    # must not raise
    assert (model.state, model.docked) == before


# -- agent-surface metadata ----------------------------------------------------

def test_tool_catalog_marks_actuations_writable(hal):
    cat = {t["op"]: t for t in hal.tool_catalog() if t["device"] == "dut"}
    for op_name in ("start_cleaning", "dock"):
        assert op_name in cat, f"op {op_name} missing from tool catalog"
        assert cat[op_name]["annotations"]["readOnlyHint"] is False, \
            f"{op_name} changes physical robot state; must NOT be readOnlyHint"
    for op_name in ("get_battery_percent", "get_clean_state"):
        assert op_name in cat, f"op {op_name} missing from tool catalog"
        assert cat[op_name]["annotations"]["readOnlyHint"] is True, \
            f"{op_name} is a safe-to-poll read; must be readOnlyHint"


# -- conformance ----------------------------------------------------------------

def test_conformance_report_ok():
    from shal import conformance
    report = conformance.check_driver("ecovacs,deebot-n20", topology=str(TOPO))
    assert report.ok, str(report)
