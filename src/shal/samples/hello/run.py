"""Hello: load a simulated temperature sensor and read it once.

Run it from any folder; it finds its topology.yaml next to itself.
"""
from pathlib import Path

import shal

TOPOLOGY = Path(__file__).resolve().parent / "topology.yaml"

with shal.load(str(TOPOLOGY)) as hal:
    celsius = hal.get_device("ambient_temp").read_celsius()
    print(f"ambient_temp: {celsius:.2f} celsius")
