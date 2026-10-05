"""The arena runner (issue #310 Scope): "gives the agent the datasheets, card
description and question; runs the ADK-style driver check; lights a tile when
a driver passes; takes the answer; writes the run record."

No fault injection and no card simulation here (issue #310 "Out of scope");
the fault the seed picks is bookkeeping only — which `card.faults` entry
`answer` compares the player's guess against. A later arena ticket makes the
instruments actually behave like the picked fault.
"""
from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from typing import Any

from shal.conformance import check_driver as _conformance_check_driver

from .cases import CaseSpec, resolve_case
from .errors import ArenaError, CheckCouldNotRun
from .loader import load_task
from .schema import Card, Instrument
from .store import DEFAULT_STATE_DIR, RunStore


class NotSupported(ArenaError):
    """A task shape this ticket's runner cannot score yet (issue #310 is the
    runner and format; fault injection / card simulation ship later)."""

    exit_code = 3


def _read_datasheet(case: CaseSpec) -> str:
    files = sorted(case.docs_dir.glob("*"))
    if not files:
        return ""
    return "\n\n".join(f.read_text(encoding="utf-8") for f in files)


def pick_fault(card: Card, seed: int) -> str:
    """The fault a seed picks, deterministically — the one piece of the
    challenge that must never sit on disk while a run is open. `start_run`
    picks it to know nothing persistent about it; `answer` picks it again,
    from the run's own stored seed, at the moment it is needed (CTO review
    on #319: a prior version persisted this to a ``*.secret.json``, which
    was on disk, hence readable, for the run's whole open lifetime)."""
    return random.Random(seed).choice([f.id for f in card.faults])


def _instrument_view(instrument: Instrument) -> dict[str, Any]:
    case = resolve_case(instrument.case)
    view = {
        "address": instrument.address,
        "case": instrument.case,
        "replacement_usd": instrument.replacement_usd,
        "datasheet": _read_datasheet(case),
    }
    if instrument.drives is not None:
        view["drives"] = instrument.drives
    else:
        view["probe"] = instrument.probe
    return view


def start_run(task_path: str, *, seed: int | None = None,
              state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Load ``task_path``, pick the hidden fault deterministically from the
    seed, and open a run. Returns exactly what the player-facing surface may
    see: task text, card description, question, instrument list (with each
    case's datasheet) and the run id — never the fault (DoD 4)."""
    loaded = load_task(task_path)
    task, card = loaded.task, loaded.card
    seed_used = task.seed if seed is None else seed

    store = RunStore(state_dir)
    state = store.create(task_path=str(loaded.task_path), card_path=str(loaded.card_path),
                         seed=seed_used)
    return {
        "ok": True,
        "side_effect": "write",
        "run_id": state.run_id,
        "task": {
            "id": task.id,
            "title": task.title,
            "level": task.level,
            "card_description": card.description,
            "question": task.question.text,
        },
        "instruments": [_instrument_view(i) for i in task.instruments],
        "limits": {"max_turns": task.limits.max_turns, "max_minutes": task.limits.max_minutes},
    }


def _import_driver_file(path: str | Path):
    path = Path(path).resolve()
    if not path.is_file():
        raise CheckCouldNotRun(f"driver file not found: {path}",
                               fix="pass the path to the driver.py you wrote")
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise CheckCouldNotRun(f"could not import {path} as a Python module",
                               fix="make sure the file is valid Python named *.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:  # noqa: BLE001 - a bad driver import is a clean, named failure
        raise CheckCouldNotRun(f"failed importing {path}: {type(e).__name__}: {e}",
                               fix="fix the error raised while importing your driver.py, "
                                   "then run the check again") from e
    return module


def check_instrument_driver(run_id: str, address: str, driver_path: str | Path, *,
                            state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """The ADK-style driver check (issue #310 Scope): import the player's
    ``driver.py`` for ``address``, bind it to the case's reference sim
    (``harness/topology.yaml``), and run `shal.conformance.check_driver`
    against it. Lights the address's tile when the report has no problems."""
    store = RunStore(state_dir)
    state = store.load(run_id)
    loaded = load_task(state.task_path)
    instrument = next((i for i in loaded.task.instruments
                       if str(i.address) == str(address)), None)
    if instrument is None:
        known = ", ".join(str(i.address) for i in loaded.task.instruments)
        raise CheckCouldNotRun(f"no instrument at address {address!r} on run {run_id!r}",
                               fix=f"use one of this run's addresses: {known}")
    case = resolve_case(instrument.case)
    _import_driver_file(driver_path)
    try:
        report = _conformance_check_driver(case.compatible, topology=str(case.harness_topology))
    except Exception as e:  # noqa: BLE001 - the check itself could not run
        raise CheckCouldNotRun(f"check could not run: {type(e).__name__}: {e}",
                               fix="fix the error above in your driver.py and run the "
                                   "check again") from e
    store.set_tile(run_id, str(address), case=instrument.case, passed=report.ok)
    return {
        "ok": True,
        "side_effect": "write",
        "run_id": run_id,
        "address": instrument.address,
        "case": instrument.case,
        "passed": report.ok,
        "problems": report.problems,
        "warnings": report.warnings,
    }


def answer(run_id: str, value: str, *, state_dir: str | Path = DEFAULT_STATE_DIR
          ) -> dict[str, Any]:
    """Close the run: compare ``value`` against the hidden fault and write the
    record (issue #310 Scope: "takes the answer; writes the run record")."""
    store = RunStore(state_dir)
    state = store.load(run_id)
    loaded = load_task(state.task_path)
    if loaded.task.question.answer.kind != "enum":
        raise NotSupported(
            f"run {run_id!r}: task.question.answer.kind "
            f"{loaded.task.question.answer.kind!r} is not scored yet",
            fix="number-kind answers need card simulation, which ships in a later "
                "arena ticket; this ticket scores enum-kind tasks only")
    fault_id = pick_fault(loaded.card, state.seed)
    record = store.answer(run_id, given=value, fault_id=fault_id)
    return {"ok": True, "side_effect": "write", **record}
