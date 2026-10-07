"""Reference sim for the ``dmm`` ADK case (harness — not given to the
player). Answers a ``MEAS:VOLT:DC?`` query, same role the ``rigol_dp832``
ADK reference's ``sim.py`` plays for that case.

issue #312: the reading is fault-aware, but only when the harness that binds
this model carries a ``config:`` (written by `fault.harness_for_run`, never
to disk — see `runner._topology_for_instrument`). With no ``config:`` (the
static, un-wired harness `shal-arena check` falls back to for any instrument
not probing the run's faulted rail) it reads the fixed 3.3 V it always has,
same as issue #310 shipped it.

issue #477: an ``open_v`` in that ``config:`` is the ``open`` fault (an open
circuit on the card): the DMM still answers, with that near-0 V value.

issue #478: a ``probe_v`` is the ``broken_probe`` fault: the probe itself is
broken, so the DMM answers that near-0 V value whatever the card does."""
from __future__ import annotations

import random

from shal.buses.sim_scpi import scpi_sim_model


@scpi_sim_model("arena,bench-dmm1")
class BenchDmm1Model:
    READING_V = 3.300000

    def __init__(self) -> None:
        self._nominal_v = self.READING_V
        self._shift_v = 0.0
        self._ripple_vpp = 0.0
        self._rng: random.Random | None = None
        self._open_v: float | None = None
        self._probe_v: float | None = None

    def bind_sim(self, bus, node) -> None:  # noqa: ARG002 - bus unused, same hook shape as core's
        config = node.spec.get("config") or {}
        self._nominal_v = config.get("nominal_v", self.READING_V)
        self._shift_v = config.get("shift_v", 0.0)
        self._ripple_vpp = config.get("ripple_vpp", 0.0)
        self._open_v = config.get("open_v")
        self._probe_v = config.get("probe_v")
        if self._ripple_vpp:
            self._rng = random.Random(config.get("seed"))

    def scpi(self, cmd: str) -> str:
        if cmd.strip() != "MEAS:VOLT:DC?":
            return ""
        if self._open_v is not None:
            return f"{self._open_v:.6f}"
        if self._probe_v is not None:
            return f"{self._probe_v:.6f}"
        value = self._nominal_v + self._shift_v
        if self._ripple_vpp and self._rng is not None:
            value += self._rng.uniform(-self._ripple_vpp / 2, self._ripple_vpp / 2)
        return f"{value:.6f}"
