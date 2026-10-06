"""Reference sim for the ``relay-modbus`` ADK case (harness — not given to
the player). Answers the coil read/write commands ``docs/datasheet.md``
describes, as plain dicts over ``shal,sim-msg`` (no Modbus byte framing, no
``pymodbus``, no TCP) — adapted from the real coil semantics documented in
determlab/adk-lab's ``cases/relay-modbus``.

Channel 0 is wired to this card's own power: ``runner.call_op`` reads this
model's ``coils`` dict back after any call on a ``power_switch`` case
(``cases.CaseSpec``) and keeps ``CardSim.power_on`` in sync with it."""
from __future__ import annotations

from shal.buses.sim_msg import msg_sim_model


@msg_sim_model("arena,bench-relay1")
class BenchRelay1Model:
    def __init__(self) -> None:
        # channel 0 starts energized: a player who never touches the relay
        # sees the same card power a task with no relay at all would have.
        self.coils: dict[int, bool] = {0: True}

    def handle(self, msg: dict) -> dict:
        fc = msg.get("fc")
        if fc == 1:  # Read Coils
            address = int(msg["address"])
            count = int(msg.get("count", 1))
            bits = [bool(self.coils.get(address + i, False)) for i in range(count)]
            return {"fc": 1, "bits": bits}
        if fc == 5:  # Write Single Coil
            address = int(msg["address"])
            value = bool(msg["value"])
            self.coils[address] = value
            return {"fc": 5, "address": address, "value": value}
        raise ValueError(f"arena,bench-relay1: unsupported function code {fc!r}")
