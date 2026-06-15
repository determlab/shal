"""Curated zero-config entry (issue #29): find a bundled *hero* device on the LAN
and build a one-node topology for it — so a user can say "control my Sonos" with
no YAML and no code.

This is deliberately **per-device** discovery, each using that device's own
library (e.g. `soco` for Sonos). It is NOT a general auto-discovery engine and
not a device database — those are explicitly out of scope for v1. Adding a hero
means adding one small entry to ``_DEVICES``.
"""
from __future__ import annotations

from collections.abc import Callable


def _discover_sonos() -> list[str]:
    import soco  # lazy: only needed to actually scan the LAN (pyshal[sonos])
    zones = soco.discover() or set()
    return sorted(z.ip_address for z in zones)


# device key -> (compatible string, LAN discovery function)
_DEVICES: dict[str, dict[str, object]] = {
    "sonos": {"compatible": "sonos,speaker", "discover": _discover_sonos},
}


def supported() -> list[str]:
    """The curated hero devices that support zero-config discovery."""
    return sorted(_DEVICES)


def _device(name: str) -> dict[str, object]:
    if name not in _DEVICES:
        raise ValueError(
            f"unknown device '{name}' — curated discovery supports {supported()}; "
            f"for anything else, write a topology YAML")
    return _DEVICES[name]


def discover(name: str) -> list[str]:
    """Discover addresses for a curated device on the LAN (may be empty)."""
    fn = _device(name)["discover"]
    assert isinstance(fn, Callable)
    return list(fn())


def build_topology(name: str, addresses: list[str]) -> dict:
    """A one-hero topology document (loadable via ``shal.load``) from the given
    addresses. Multiple speakers get numbered ids (sonos_1, sonos_2, …)."""
    compatible = _device(name)["compatible"]
    if not addresses:
        raise ValueError(f"no addresses for '{name}'")
    root: dict[str, dict] = {}
    single = len(addresses) == 1
    for i, addr in enumerate(addresses, start=1):
        key = name if single else f"{name}_{i}"
        root[key] = {"id": key, "driver": compatible, "address": addr}
    return {"shal_version": 1, "root": root}
