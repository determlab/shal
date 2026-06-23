"""Hal.warm() eagerly activates transports so the first MCP read isn't a hang-then-warm
(#83). Uses the deebot sim (a real Transport bus) as the worked example.
"""
import sys
from pathlib import Path

import shal

_DEEBOT = Path(__file__).resolve().parents[1] / "examples" / "demos" / "deebot"


def _load_sim():
    sys.path.insert(0, str(_DEEBOT))
    import deebot_driver  # noqa: F401  registers ecovacs,deebot
    import sim_cloud  # noqa: F401  registers playground,sim-cloud
    return shal.load(str(_DEEBOT / "deebot_sim.yaml"))


def test_warm_activates_the_bus_and_reports_no_failures():
    with _load_sim() as hal:
        bus = hal.get_device("/ecovacs_sim")   # the sim-cloud bus node (reached by path)
        assert hal.warm() == []                # warms cleanly, nothing failed
        assert bus.is_active()                 # the first real read now pays no connect cost
        assert hal.warm() == []                # idempotent: a second warm is a no-op
