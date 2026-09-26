# In a terminal this asks you. In CI, or in a pipe, there is nobody to ask,
# so it refuses and exits 2.
"""Limits: measure, PASS; a reading outside the limit, FAIL; then a write through the gate.

Three things happen here, and the third is the point.

A measurement on its own is not a test. A test is a measurement against a limit,
and a verdict. So the same reading is checked twice against two different
limits: the room is within the range a room should be in, and outside the range
a curing oven should be in. One PASS, one FAIL, from one sensor.

Then the sample tries to change the device — and does not get to.
`set_target` is labelled `config` in its driver, so it stops and asks a person
before anything reaches the device. Nothing is sent. The reading is unchanged.

**A refusal is the product working, not a crash.** That is why this exits 2 with
one line, rather than raising. An agent, a CI job or a person all learn the same
thing from it: the operation was refused, and why.

Run it on real hardware by changing the topology, not this file.

Next: `shal docs --sample jig` tests several units and logs one record each.
"""
from pathlib import Path

import shal

TOPOLOGY = Path(__file__).resolve().parent / "topology.yaml"


def check(name: str, celsius: float, low: float, high: float) -> None:
    """Print one verdict. This is what a test step is: a value, a limit, a word."""
    verdict = "PASS" if low <= celsius <= high else "FAIL"
    print(f"{verdict}  {name}: {celsius:.2f} C (limit {low} to {high} C)")


with shal.load(str(TOPOLOGY)) as hal:
    room = hal.get_device("room")
    # The same sensor, two limits. A simulated room sits near 25 C, so it is
    # inside a room's range and outside an oven's.
    check("room temperature", room.read_celsius(), 15.0, 35.0)
    check("curing temperature", room.read_celsius(), 30.0, 40.0)
    # Now a write. `set_target` is labelled `config`, so the gate stops it
    # below the driver — before anything reaches the device.
    try:
        room.set_target(30.0)
        print("approved: the sim room now drifts toward 30.0 C")
    except shal.ApprovalDenied as e:
        print(f"refused: {e}")
        raise SystemExit(2) from None
