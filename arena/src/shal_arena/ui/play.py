"""Play (issue #407): the write routes `shal-arena ui` serves so a person
can play a task with buttons instead of the CLI. Routes and controls only
-- the page's own visuals follow #447 once approved (CTO design review on
#407's mock, comment 2026-10-07).

Every button here calls the SAME runner.py functions `shal-arena`'s CLI
calls (Constraints: "no second game logic") -- this module only resolves
WHICH instrument gets WHICH control and WHICH reference driver, then hands
off to `runner.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..cases import resolve_case
from ..errors import ArenaError
from ..loader import list_tasks, resolve_task
from ..runner import answer as runner_answer
from ..runner import call_op, drive_input, has_any_measurement, start_run, take_measurement
from ..store import DEFAULT_STATE_DIR

_EXAMPLES = Path(__file__).resolve().parents[3] / "examples"

#: CTO review on #407's mock, condition 2: a person playing has no driver.py
#: of their own, so every control uses the packaged reference driver for
#: that instrument's case ("using the reference drivers", said once on the
#: page). All 4 packaged cases (`cases.CASES`) already ship one; if a case
#: had none, adding it here would be in scope.
REFERENCE_DRIVERS: dict[str, Path] = {
    "scpi-psu": _EXAMPLES / "reference_driver" / "driver.py",
    "dmm": _EXAMPLES / "minimal_dmm_driver.py",
    "relay-modbus": _EXAMPLES / "minimal_relay_driver.py",
    "sht31": _EXAMPLES / "minimal_temp_driver.py",
}

#: One label per CASE (condition 1: never per address -- nothing hardcoded
#: to psu0/dmm0). A power-switch case (relay-modbus) is "switch": `call
#: set_relay`, never `drive` -- `drive_input`'s own gate refuses a power
#: switch anyway (issue relay-rail / PR #426).
_LABELS = {
    "scpi-psu": "Set voltage",
    "dmm": "Measure",
    "relay-modbus": "Switch relay",
    "sht31": "Read temperature",
}


class PlayError(ArenaError):
    """A Play-route-only refusal: the route itself declines to call the
    runner (an unknown case, an answer with no measurement yet) -- never a
    new scoring/gate rule, just this module's own routing failing closed."""


def reference_driver_for(case_name: str) -> Path:
    try:
        return REFERENCE_DRIVERS[case_name]
    except KeyError:
        known = ", ".join(sorted(REFERENCE_DRIVERS))
        raise PlayError(f"play: no reference driver for case {case_name!r}",
                        fix=f"use one of the packaged cases: {known}") from None


def instrument_controls(instruments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One control descriptor per instrument, built from the run's own
    `instruments` list (condition 1) -- never a fixed psu0/dmm0 pair. Each
    entry: `address`, `case`, `kind` (`drive`|`measure`|`switch`), `label`."""
    controls = []
    for inst in instruments:
        case_name = inst["case"]
        case = resolve_case(case_name)
        if case.power_switch:
            kind = "switch"
        elif inst.get("drives") is not None:
            kind = "drive"
        else:
            kind = "measure"
        controls.append({"address": inst["address"], "case": case_name, "kind": kind,
                         "label": _LABELS.get(case_name, case_name)})
    return controls


def list_play_tasks() -> list[dict[str, str]]:
    """Frame 1 of the mock, "pick a task": the same packaged list `shal-arena
    tasks` already prints (`loader.list_tasks`) -- no second catalogue."""
    return list_tasks()


def start(task: str, *, state_dir: str | Path = DEFAULT_STATE_DIR,
         seed: int | None = None) -> dict[str, Any]:
    """"Start task": the CLI's own `start_run`, given a packaged name or a
    path the same way `shal-arena run` takes one."""
    return start_run(str(resolve_task(task)), seed=seed, state_dir=state_dir)


def measure(run_id: str, address: str, case_name: str, *,
           state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """"Measure" / "Read temperature": the CLI's own `take_measurement`,
    with the reference driver resolved from the instrument's case."""
    driver = reference_driver_for(case_name)
    return take_measurement(run_id, address, driver, state_dir=state_dir)


def drive(run_id: str, address: str, volts: float, *,
         state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """"Set voltage": the CLI's own `drive_input` -- its own damage gate
    decides "nothing was sent", the same as `shal-arena drive`."""
    return drive_input(run_id, address, volts, state_dir=state_dir)


def switch(run_id: str, address: str, case_name: str, on: bool, *,
          state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """"Switch relay on/off": the CLI's own `call_op`, running `set_relay`
    on channel 0 (this card's own power relay -- AGENTS.md's bench-relay1
    datasheet: "Channel 0 is this card's power relay"), the only coil a
    Play button ever needs to reach."""
    driver = reference_driver_for(case_name)
    return call_op(run_id, address, driver, "set_relay", ["0", "true" if on else "false"],
                  state_dir=state_dir)


def answer(run_id: str, value: str, *, state_dir: str | Path = DEFAULT_STATE_DIR
          ) -> dict[str, Any]:
    """"Answer": refuses BEFORE calling the CLI's own `runner.answer` (which
    never refuses -- it always closes the run, marking `disqualified` when
    there was no measurement) if this run has no measurement yet, from the
    exact rule `runner.has_any_measurement` already enforces for that same
    field, never a second one. A real answer still goes through the one
    scoring function the CLI itself calls."""
    if not has_any_measurement(run_id, state_dir=state_dir):
        raise PlayError(
            f"run {run_id!r}: no measurement yet",
            fix="measure a probe instrument at least once before answering (the run "
                "would otherwise be disqualified, and answering closes it for good)")
    return runner_answer(run_id, value, state_dir=state_dir)
