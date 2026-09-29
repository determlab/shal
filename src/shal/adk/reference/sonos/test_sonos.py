"""Tests for the Sonos reference driver, run as its twin (`address: sim`).

No speaker on the network and no `soco` installed: the twin in sim.py stands in
for the library. Run from anywhere: this file puts its own folder on sys.path, so
`driver` and `sim` are the two files next to it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402,F401  (registers sonos,speaker)

import shal  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def spk():
    with shal.load(_TOPO) as hal, shal.approver(shal.AutoApprove()):
        yield hal.get_device("sonos")   # playback ops are gated: approve them


_ACTUATORS = {"play": (), "pause": (), "stop": (), "next_track": (),
              "previous_track": (), "set_volume": (40,)}


def test_playback_and_volume_ops_are_actuators():
    with shal.load(_TOPO) as hal:
        effects = {t["op"]: t["side_effect"] for t in hal.tool_catalog()}
    for op_name in _ACTUATORS:
        assert effects[op_name] == "actuator", op_name
    for op_name in ("get_volume", "get_state", "now_playing"):
        assert effects[op_name] == "none", op_name


@pytest.mark.parametrize("op_name", sorted(_ACTUATORS))
def test_actuators_are_refused_under_the_deny_approver(spk, op_name):
    with shal.approver(shal.DenyAll()), pytest.raises(shal.ApprovalDenied):
        getattr(spk, op_name)(*_ACTUATORS[op_name])
    assert spk.get_state() == "STOPPED"            # the twin never saw it
    assert spk.get_volume() == 25


def test_reads_come_from_the_twin(spk):
    assert spk.get_state() == "STOPPED"
    assert spk.get_volume() == 25
    assert spk.now_playing() == {"title": "Aja", "artist": "Steely Dan", "album": "Aja"}


def test_play_is_undone_by_pause(spk):
    spk.play()
    assert spk.get_state() == "PLAYING"
    spk.pause()
    assert spk.get_state() == "PAUSED_PLAYBACK"


def test_set_volume_reads_back(spk):
    spk.set_volume(40)
    assert spk.get_volume() == 40


@pytest.mark.parametrize("level", [-1, 101])
def test_set_volume_out_of_range_is_rejected_before_the_library(spk, level):
    with pytest.raises(shal.LimitError):
        spk.set_volume(level)
    assert spk.get_volume() == 25                  # the twin never saw it


def test_is_a_media_player(spk):
    assert isinstance(spk, shal.MediaPlayer)


def test_a_library_network_error_is_a_hop_error(spk, monkeypatch):
    client = spk._client_obj()

    def lost(self=None):
        raise OSError("host unreachable")

    monkeypatch.setattr(client, "get_current_transport_info", lost)
    with pytest.raises(shal.HopError) as exc:
        spk.get_state()
    assert exc.value.delivered == "unknown"


def test_the_twin_needs_no_soco(spk):
    spk.play()
    assert "soco" not in sys.modules


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.SonosSpeaker, _TOPO)
    assert report.problems == []
    assert report.warnings == []
