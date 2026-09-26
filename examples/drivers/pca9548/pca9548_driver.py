"""nxp,pca9548 — an 8-channel I2C mux: a node that is also a bus (DESIGN V2 'Muxes').

The chip moved here from ``shal.buses.mux`` (#149, ADK §3.6): it is a vendor part,
so it does not ship. The mechanism it uses stays in core — ``MuxState`` (one per
physical mux, shared by its channels only) and ``MuxChannel`` (one bus per port,
selection inside the call under the mux's lock).
"""
from __future__ import annotations

from shal import registry
from shal.buses.mux import MuxChannel, MuxState
from shal.driver import Driver
from shal.errors import LoadError
from shal.log import redact_url
from shal.node import Node
from shal.transport import ByteTransport, Transport


@registry.register
class Pca9548(Driver):
    """The mux driver itself is NOT a Transport; it provides one bus per channel."""

    compatible = "nxp,pca9548"
    kind = ByteTransport
    N_CHANNELS = 8

    def __init__(self) -> None:
        self._state = MuxState()  # per physical mux

    def provide_child_bus(self, child: Node) -> Transport:
        ch = child.address
        if not isinstance(ch, int) or not (0 <= ch < self.N_CHANNELS):
            # redact_url: child address is ${ENV}-resolved like any other
            # address, so its content isn't constrained by the expected int
            # grammar (#126)
            raise LoadError(f"{child.path}: pca9548 channel must be 0-"
                            f"{self.N_CHANNELS - 1}, got {redact_url(str(ch))!r}")
        return MuxChannel(child, upstream=self.bus, state=self._state,
                          channel=ch, mux_addr=self.addr)
