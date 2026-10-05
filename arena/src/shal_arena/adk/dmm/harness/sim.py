"""Reference sim for the ``dmm`` ADK case (harness — not given to the player).
Answers a fixed, deterministic reading: card simulation and fault injection
are out of scope for this ticket (issue #310), so this model is not wired to
any PSU or rail — it only exercises a correctly-written driver's
``MEAS:VOLT:DC?`` query, same role the ``rigol_dp832`` ADK reference's
``sim.py`` plays for that case.
"""
from __future__ import annotations

from shal.buses.sim_scpi import scpi_sim_model


@scpi_sim_model("arena,bench-dmm1")
class BenchDmm1Model:
    READING_V = 3.300000

    def scpi(self, cmd: str) -> str:
        if cmd.strip() == "MEAS:VOLT:DC?":
            return f"{self.READING_V:.6f}"
        return ""
