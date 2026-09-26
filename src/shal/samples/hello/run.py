"""Hello: load a simulated temperature sensor and read it once.

The smallest true thing SHAL does, and the whole idea is in two files.

`topology.yaml` describes the setup. This file talks to that description — never
to a device, an address or a transport. Point the topology at a real sensor on a
real bus and this code does not change.

There is no hardware here: the sensor is simulated and ships with SHAL. The
reading drifts, so run it twice and the number moves.

Next: `shal docs --sample limits` adds a limit, a verdict, and a write that the
gate stops.
"""
from pathlib import Path

import shal

TOPOLOGY = Path(__file__).resolve().parent / "topology.yaml"

with shal.load(str(TOPOLOGY)) as hal:
    celsius = hal.get_device("ambient_temp").read_celsius()
    print(f"ambient_temp: {celsius:.2f} celsius")
