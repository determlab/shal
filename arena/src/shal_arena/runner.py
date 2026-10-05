"""The arena runner (issue #310 Scope): "gives the agent the datasheets, card
description and question; runs the ADK-style driver check; lights a tile when
a driver passes; takes the answer; writes the run record."

issue #312 adds fault injection at run time (`_topology_for_instrument`,
`fault.py`), the sim log (`simlog.py`, populated by `check_instrument_driver`
and `_take_measurement`), and the score file (`score.py`, written by
`answer`). Card simulation proper (the rail's own circuit behaviour under
load, damage) is still out of scope here — a later arena ticket's job.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from shal.conformance import check_driver as _conformance_check_driver

from . import fault as _fault
from .cases import CaseSpec, resolve_case
from .errors import ArenaError, CheckCouldNotRun
from .loader import load_task
from .schema import Card, Instrument, Task
from .score import build_score
from .simlog import SimLog
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
    was on disk, hence readable, for the run's whole open lifetime).

    Delegates to `fault.realized_fault` (issue #312), which draws from the
    exact same `random.Random(seed).choice(card.faults)` call this used to
    make directly — same seed, same id, for every existing caller."""
    return _fault.realized_fault(card, seed).fault_id


def _topology_for_instrument(task: Task, card: Card, instrument: Instrument,
                             seed: int, case: CaseSpec) -> str | dict:
    """issue #312: the harness this instrument's `check` runs against for
    THIS run. An instrument with no `probe:` (it `drives:` a card input
    instead) never carries a fault — always the case's static harness.  A
    `probe:` instrument gets the static harness too UNLESS the seed's
    realized fault targets the exact rail its wiring names; only then does
    it get the in-memory, fault-wired topology from `fault.harness_for_run`
    (never written to disk — Scope: "never written to a file the player or
    agent can read")."""
    static = str(case.harness_topology)
    if instrument.probe is None:
        return static
    realized = _fault.realized_fault(card, seed)
    rail = _fault.rail_for_fault(card, realized)
    if rail is None or instrument.probe != f"card.{rail.test_point}":
        return static
    return _fault.harness_for_run(case, rail=rail, realized=realized, seed=seed)


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


def _take_measurement(compatible: str, topology: str | dict, sim_log: SimLog,
                      address: str) -> None:
    """issue #312: actually invoke the bound driver's own read op once, so
    `check`ing a probe instrument always leaves a genuine entry in the sim
    log — not just whatever `shal.conformance`'s generic structural probes
    happened to touch. Opens its own `shal.load(topology)` (a fresh bus/sim
    instance; conformance's own is already closed by the time this runs), so
    the very call that lands in the log is this one read, named by the
    driver's compatible — one zero-argument op with ``side_effect="none"``,
    the convention both packaged ADK cases (`scpi-psu`, `dmm`) follow for
    "the thing you measure."

    An instrument wired as ``fault: unplugged`` (the `open` fault) raises
    before any exchange reaches the sim's own log line; that failure IS the
    measurement attempt, so it is logged here directly, as a fixed `attempt`
    marker that never repeats the exception's own text (Scope: no file the
    player/agent can read may name the fault)."""
    from shal import registry
    from shal.driver import inferred_side_effect
    from shal.hal import load as _load

    cls = registry.resolve(compatible)
    read_op = next((name for name, fn in cls.capability_ops().items()
                    if inferred_side_effect(fn) == "none"
                    and not (getattr(fn, "__shal_op__", {}) or {}).get("params")), None)
    if read_op is None:
        return
    try:
        with sim_log.record_for(address), _load(topology) as hal:
            node = next((n for root in hal._roots for n in root.walk()
                        if isinstance(n.driver, cls)), None)
            if node is not None:
                getattr(node.driver, read_op)()
    except Exception:  # noqa: BLE001 - unreachable (open) is expected, not a bug
        sim_log.append(address=address, kind="attempt", cmd="unreachable")


def check_instrument_driver(run_id: str, address: str, driver_path: str | Path, *,
                            state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """The ADK-style driver check (issue #310 Scope): import the player's
    ``driver.py`` for ``address``, bind it to the case's reference sim
    (``harness/topology.yaml``), and run `shal.conformance.check_driver`
    against it. Lights the address's tile when the report has no problems.

    issue #312: the topology it binds to is THIS run's (`_topology_for_
    instrument` — the static harness, or an in-memory fault-wired one), and
    every SCPI exchange the check makes is appended to the run's sim log —
    the proof `answer` later checks for before crediting a measurement."""
    store = RunStore(state_dir)
    store.increment_turns(run_id)
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
    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case)
    sim_log = SimLog(store.sim_log_path(run_id))
    try:
        report = _conformance_check_driver(case.compatible, topology=topology)
    except Exception as e:  # noqa: BLE001 - the check itself could not run
        raise CheckCouldNotRun(f"check could not run: {type(e).__name__}: {e}",
                               fix="fix the error above in your driver.py and run the "
                                   "check again") from e
    if instrument.probe is not None:
        _take_measurement(case.compatible, topology, sim_log, str(address))
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
    record (issue #310 Scope: "takes the answer; writes the run record").

    issue #312 adds: ``disqualified`` (true when the sim log has no logged
    measurement — query or unreachable-attempt — at any probe instrument's
    address: the player never took a measurement this answer could be based
    on) and ``score`` (the 13-field score file, also written to
    ``<run_id>.score.json``). Neither changes ``record``/``correct`` itself —
    every existing caller of `answer` keeps seeing exactly what it always
    returned, under the same keys."""
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

    sim_log = SimLog(store.sim_log_path(run_id))
    probe_addresses = [str(i.address) for i in loaded.task.instruments if i.probe is not None]
    disqualified = bool(probe_addresses) and not any(
        sim_log.has_measurement(addr) for addr in probe_addresses)
    score = build_score(task_id=loaded.task.id, seed=state.seed, fault_id=fault_id,
                        given=value, correct=record["correct"], disqualified=disqualified,
                        created_at=state.created_at, closed_at=record["closed_at"],
                        turns=state.turns, record_path=store.record_path(run_id))
    store.write_score(run_id, score)
    return {"ok": True, "side_effect": "write", **record,
            "disqualified": disqualified, "score": score, "sim_log": str(sim_log.path)}
