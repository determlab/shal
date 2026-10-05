"""The "without SHAL" side of the benchmark (issue #314): raw SCPI, socket-like.

`RawScpi.send(address, cmd)` is one text command to the instrument at
``address`` and its text reply — no driver, no gate, no SHAL record. It talks
to the very same sim the SHAL side reaches: the case's own ``@scpi_sim_model``
(`cases.resolve_case` imports it), wired with the run's seed-realized fault
(`runner._topology_for_instrument`), and the same `CardSim` damage model, so a
``VOLT 30`` on the 5 V card destroys it here exactly as `drive` does there.

Every command received is appended to the run's sim log in `simlog.SimLog`'s
JSON-lines shape (``ts``/``address``/``kind``/``cmd``), so the two sides' logs
can be read by the same tool. Each command is one turn (`RunStore.
increment_turns`), counted before the sim is reached, like every other call.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from shal.buses.sim_scpi import SCPI_SIM_MODELS

from .. import fault as _fault
from ..cases import resolve_case
from ..errors import CheckCouldNotRun, MeasurementFailed
from ..loader import load_task
from ..runner import _fault_is_unplugged, _load_card_sim, _topology_for_instrument
from ..simlog import SimLog
from ..store import DEFAULT_STATE_DIR, RunStore

_SET_V = re.compile(r"^VOLT\s+([0-9.eE+-]+)$")


class RawScpi:
    """One run's raw-SCPI session (open the run with `runner.start_run`)."""

    def __init__(self, run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR) -> None:
        self.run_id = run_id
        self.store = RunStore(state_dir)
        self._models: dict[str, Any] = {}

    def send(self, address: str, cmd: str) -> str:
        state = self.store.increment_turns(self.run_id)
        loaded = load_task(state.task_path)
        instrument = next((i for i in loaded.task.instruments
                           if str(i.address) == str(address)), None)
        if instrument is None:
            known = ", ".join(str(i.address) for i in loaded.task.instruments)
            raise CheckCouldNotRun(f"no instrument at address {address!r} on run {self.run_id!r}",
                                   fix=f"use one of this run's addresses: {known}")
        case = resolve_case(instrument.case)
        card_sim = _load_card_sim(loaded, state, self.store, self.run_id)

        model = self._models.get(str(address))
        if model is None:
            topology = _topology_for_instrument(loaded.task, loaded.card, instrument,
                                                state.seed, case)
            if isinstance(topology, dict) and _fault_is_unplugged(topology):
                raise MeasurementFailed(
                    f"no answer from the instrument at {address!r}",
                    fix="the instrument did not answer; check the link or try another instrument")
            model = self._build_model(case.compatible, topology)
            self._models[str(address)] = model
        rail = (next((r for r in loaded.card.rails
                      if instrument.probe == f"card.{r.test_point}"), None)
                if instrument.probe is not None else None)
        if rail is not None and rail.nominal_v != 0 and card_sim.rail_voltage(rail.name) == 0.0:
            # card in protection or destroyed: the rail reads dead (same as `measure`)
            model = self._build_model(case.compatible, _fault.harness_for_run(
                case, rail=rail, seed=state.seed,
                realized=_fault.RealizedFault("dead", {"shift_v": -rail.nominal_v})))

        sim_log = SimLog(self.store.sim_log_path(self.run_id))
        if instrument.probe is not None:
            sim_log.mark_measured(str(address))  # a raw read is the deliberate read
        sim_log.log_command(str(address), cmd)
        reply = model.scpi(cmd)

        match = _SET_V.match(cmd.strip())
        if match and instrument.drives is not None:
            card_sim.apply_input(instrument.drives.removeprefix("card."), float(match.group(1)),
                                 address=str(address))
            self.store.set_card_state(self.run_id, applied=card_sim.applied,
                                      destroyed=card_sim.destroyed)
        return reply

    @staticmethod
    def _build_model(compatible: str, topology: str | dict) -> Any:
        model = SCPI_SIM_MODELS[compatible]()
        bind_sim = getattr(model, "bind_sim", None)
        if bind_sim is not None:
            child: dict = {}
            if isinstance(topology, dict):
                bench = next(iter(topology["root"].values()))
                child = next(iter(bench["children"].values()))
            bind_sim(None, SimpleNamespace(spec=child))
        return model
