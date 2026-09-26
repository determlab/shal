# In a terminal this asks you. In CI, or in a pipe, there is nobody to ask,
# so it refuses and exits 2.
"""Limits: measure, PASS; a reading outside the limit, FAIL; then a write through the gate."""
from pathlib import Path

import shal

TOPOLOGY = Path(__file__).resolve().parent / "topology.yaml"


def check(name: str, celsius: float, low: float, high: float) -> None:
    verdict = "PASS" if low <= celsius <= high else "FAIL"
    print(f"{verdict}  {name}: {celsius:.2f} C (limit {low} to {high} C)")


with shal.load(str(TOPOLOGY)) as hal:
    room = hal.get_device("room")
    check("room temperature", room.read_celsius(), 15.0, 35.0)
    check("curing temperature", room.read_celsius(), 30.0, 40.0)
    try:
        room.set_target(30.0)
        print("approved: the sim room now drifts toward 30.0 C")
    except shal.ApprovalDenied as e:
        print(f"refused: {e}")
        raise SystemExit(2) from None
