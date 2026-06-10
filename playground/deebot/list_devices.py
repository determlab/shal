"""List the robots on the Ecovacs account — read-only, sends nothing to any robot.

    python list_devices.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import ecovacs_bus  # noqa: F401  registers ecovacs,cloud

import shal
from shal.node import Node


def _find_cloud_address(spec: dict) -> str | None:
    """The ecovacs,cloud node may sit anywhere in the tree (e.g. under the PC root)."""
    for child in spec.values():
        if not isinstance(child, dict):
            continue
        if child.get("driver") == "ecovacs,cloud":
            return child["address"]
        found = _find_cloud_address(child.get("children") or {})
        if found:
            return found
    return None


def main() -> int:
    doc = yaml.safe_load((HERE / "deebot_real.yaml").read_text(encoding="utf-8"))
    country = _find_cloud_address(doc["root"])
    if not country:
        print("no ecovacs,cloud node in deebot_real.yaml", file=sys.stderr)
        return 1
    node = Node("ecovacs", address=country)
    bus = ecovacs_bus.EcovacsCloudBus(node)
    print(f"country: {country} -> portal: {bus.portal}")
    try:
        bus.ensure_ready()  # login chain + GetDeviceList, nothing robot-bound
    except shal.HopError as e:
        print(f"FAILED: {e}", file=sys.stderr)
        return 1
    if not bus._devices:
        print("login OK, but no robots are registered on this account")
        return 0
    print(f"login OK — {len(bus._devices)} robot(s):")
    for d in bus._devices:
        print(f"  - nick: {d.get('nick')!r} | model: {d.get('deviceName')!r} | "
              f"class: {d.get('class')!r} | did: {d.get('did')!r} | "
              f"sn: {d.get('name')!r} | company: {d.get('company')!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
