"""sonos,speaker driver (#28): the first wrap-a-library hero, exercised on its
built-in 'sim' client — no soco, no hardware."""
import pytest

import shal
from shal.capabilities import MediaPlayer

_YAML = ("shal_version: 1\n"
         "root:\n"
         "  living_room: {id: sonos, driver: 'sonos,speaker', address: sim}\n")


@pytest.fixture
def hal(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as h:
        yield h


def test_transport_controls_change_state(hal):
    spk = hal.get_device("sonos")
    assert spk.get_state() == "STOPPED"
    spk.play()
    assert spk.get_state() == "PLAYING"
    spk.pause()
    assert spk.get_state() == "PAUSED_PLAYBACK"
    spk.stop()
    assert spk.get_state() == "STOPPED"


def test_volume_roundtrips(hal):
    spk = hal.get_device("sonos")
    spk.set_volume(40)
    assert spk.get_volume() == 40


def test_now_playing_shape(hal):
    np = hal.get_device("sonos").now_playing()
    assert set(np) == {"title", "artist", "album"} and np["artist"]


def test_volume_limit_rejected_pre_io(hal):
    spk = hal.get_device("sonos")
    with pytest.raises(shal.LimitError):
        spk.set_volume(150)          # over the declared maximum of 100
    assert spk.get_volume() == 25    # nothing changed — rejected before I/O


def test_implements_media_player_capability(hal):
    assert isinstance(hal.get_device("sonos"), MediaPlayer)


def test_agent_surface_classifies_ops(hal):
    cat = {t["op"]: t for t in hal.tool_catalog()}
    # reads are read-only; playback/volume are benign writes (instant, not gated)
    assert cat["get_state"]["annotations"]["readOnlyHint"] is True
    assert cat["play"]["annotations"]["destructiveHint"] is False
    assert cat["set_volume"]["annotations"]["destructiveHint"] is False
