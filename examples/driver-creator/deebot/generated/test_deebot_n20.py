"""Tests for the DEEBOT N20 driver against its sim, using the doc's worked
examples (DN20-PROTO §7) as test vectors.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import shal
import driver as drv  # noqa: E402  (registers the driver)
import sim  # noqa: E402  (registers the sim model)

TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def hal():
    h = shal.load(TOPO)  # buses activate lazily on first use
    try:
        yield h
    finally:
        h.close()


@pytest.fixture
def robot(hal):
    return hal.get_device("robot")


@pytest.fixture
def bus(hal):
    # the sim-msg bus node, for fail_next / model_for hooks
    return hal.get_device("bench")


# --- value tests (DN20-PROTO §7 worked examples) ----------------------------

def test_battery_read_bench_default(robot):
    # E1: bench default battery is 87, isLow 0.
    assert robot.get_battery_percent() == 87


def test_battery_low_reads_through(robot, bus):
    # E1 note: with battery at 9 the read returns value 9.
    bus.model_for("did-bot1").battery = 9
    assert robot.get_battery_percent() == 9


def test_state_idle_at_power_on(robot):
    # E2: activity state read while idle.
    assert robot.get_clean_state() == "idle"


def test_start_transitions_to_clean(robot):
    # E3: start an auto clean; a follow-up read reports "clean".
    robot.start_cleaning()
    assert robot.get_clean_state() == "clean"


def test_full_state_machine(robot):
    # DN20-PROTO §4: idle -> clean -> pause -> clean(resume) -> goCharging.
    robot.start_cleaning()
    assert robot.get_clean_state() == "clean"
    robot.pause()
    assert robot.get_clean_state() == "pause"
    robot.resume()
    assert robot.get_clean_state() == "clean"
    robot.dock()
    assert robot.get_clean_state() == "goCharging"


def test_stop_returns_to_idle(robot):
    robot.start_cleaning()
    robot.stop_cleaning()
    assert robot.get_clean_state() == "idle"


def test_dock_when_already_docked_succeeds(robot):
    # E4: charge while already docked answers 30007; must be SUCCESS, no raise.
    robot.dock()  # bench power-on default is docked -> 30007
    # state unchanged (still idle, the power-on state)
    assert robot.get_clean_state() == "idle"


def test_dock_while_cleaning_returns_to_charging(robot):
    robot.start_cleaning()
    robot.dock()  # now out cleaning -> code 0, state goCharging
    assert robot.get_clean_state() == "goCharging"


def test_locate(robot):
    # E5: locate chime succeeds and does not change state.
    robot.start_cleaning()
    robot.locate()
    assert robot.get_clean_state() == "clean"


# --- device refusal (E6) ----------------------------------------------------

def test_refusal_raises_deebot_error(robot):
    # E6: a non-zero result code is a robot refusal -> DeebotError (NOT a
    # HopError; delivery was certain). The driver's _command surfaces it; drive
    # an undocumented command through it to exercise the mapping.
    with pytest.raises(drv.DeebotError) as exc:
        robot._command("getFooBar")
    assert exc.value.code == 1
    # A refusal is a device error, never a transport/HopError.
    assert not isinstance(exc.value, shal.HopError)


# --- retry contract (SDK §5 / §6 hooks) -------------------------------------

def test_idempotent_read_recovers_from_fail_next(robot, bus):
    # fail_next=1 drops the next call with delivered="no"; an @idempotent read
    # is auto-retried by the framework and still succeeds.
    bus.fail_next = 1
    assert robot.get_battery_percent() == 87


def test_actuation_surfaces_delivered_unknown(robot, bus):
    # fail_delivered_unknown -> one ambiguous failure; a non-idempotent
    # actuation must propagate HopError(delivered="unknown") untouched.
    bus.fail_delivered_unknown = True
    with pytest.raises(shal.HopError) as exc:
        robot.start_cleaning()
    assert exc.value.delivered == "unknown"


# --- capability -------------------------------------------------------------

def test_isinstance_capability(robot):
    assert isinstance(robot, drv.VacuumRobot)
