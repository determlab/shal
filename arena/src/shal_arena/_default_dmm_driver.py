"""The `dmm` driver bench's built-in default policy measures with (issue
#397). Shipped inside the package (unlike `examples/`, which `pip install`
does not ship) so `shal-arena bench --runs 10` needs no driver.py of your
own. Same shape as `examples/minimal_dmm_driver.py` -- not meant to be
copied; a real player writes their own, see README.md's "Write your driver"
section. `bench.py`'s own `_default_dmm_driver_registered()` scopes this
class's registration to the default-policy call only (CTO review on #397,
round 2) -- `override=True` here is a second, redundant safety net for that,
not the mechanism itself.
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


class _DefaultBenchDmmDriver(Driver):
    compatible = "arena,bench-dmm1"
    kind = MessageTransport
    llm_ready = True

    @idempotent
    @op("Read the measured DC voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])


registry.register(_DefaultBenchDmmDriver, override=True)
