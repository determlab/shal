"""Curated zero-config entry (#29): per-device discovery, in-memory topology
building, dict loading, and the shal-mcp resolver — all exercised via `sim`,
no hardware and no soco."""
import pytest

import shal
from shal import discovery
from shal.mcp.server import _resolve_hal


def test_supported_lists_sonos():
    assert "sonos" in discovery.supported()


def test_build_topology_single_node():
    topo = discovery.build_topology("sonos", ["192.168.1.5"])
    assert topo["shal_version"] == 1
    assert topo["root"]["sonos"] == {
        "id": "sonos", "driver": "sonos,speaker", "address": "192.168.1.5"}


def test_build_topology_multiple_are_numbered():
    topo = discovery.build_topology("sonos", ["a", "b"])
    assert set(topo["root"]) == {"sonos_1", "sonos_2"}
    assert topo["root"]["sonos_2"]["address"] == "b"


def test_build_topology_unknown_device_is_loud():
    with pytest.raises(ValueError, match="unknown device"):
        discovery.build_topology("toaster", ["x"])


def test_discover_unknown_device_does_not_touch_a_library():
    with pytest.raises(ValueError, match="unknown device"):
        discovery.discover("toaster")


# ---- dict (in-memory) topology loading -------------------------------------------

def test_load_accepts_an_in_memory_dict():
    topo = discovery.build_topology("sonos", ["sim"])
    with shal.load(topo) as hal:           # a dict, not a path
        assert hal.get_device("sonos").get_state() == "STOPPED"


# ---- the curated 'control my Sonos' resolver -------------------------------------

def test_resolve_hal_curated_sim_path_works_end_to_end():
    hal = _resolve_hal(topology=None, device="sonos", address="sim")
    try:
        spk = hal.get_device("sonos")
        spk.play()
        assert spk.get_state() == "PLAYING"
    finally:
        hal.close()


def test_resolve_hal_with_no_input_exits_friendly():
    with pytest.raises(SystemExit):
        _resolve_hal(topology=None, device=None, address=None)
