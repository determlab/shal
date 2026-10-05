"""The arena runner (issue #310 Scope): "gives the agent the datasheets, card
description and question; runs the ADK-style driver check; lights a tile when
a driver passes; takes the answer; writes the run record."

issue #312 adds fault injection at run time (`_topology_for_instrument`,
`fault.py`), the sim log (`simlog.py`, populated ONLY by `take_measurement` —
the player's own deliberate read, never a side effect of `check_instrument_
driver`'s structural ADK check, per CTO review on #322), and the score file
(`score.py`, written by `answer`).

issue #313 adds `drive_input` (the `shal-arena drive` Agent path): the
card's own damage model (`card_sim.CardSim`) wired in here so an agent can
reach `apply_input`/`state` at all. It drives the card from whatever a prior
`drive_input` call on this run already applied (`RunStore.set_card_state`),
and logs to the same per-run sim log `take_measurement` writes to.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import yaml
from shal.conformance import check_driver as _conformance_check_driver

from . import fault as _fault
from .card_sim import CardSim, Dmm, catalogue
from .cases import CaseSpec, resolve_case
from .errors import ArenaError, CheckCouldNotRun, MeasurementFailed
from .loader import LoadedTask, load_task
from .schema import Card, Instrument, Task
from .score import build_score
from .simlog import SimLog
from .store import DEFAULT_STATE_DIR, RunState, RunStore


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


def _fault_is_unplugged(topology: dict) -> bool:
    bench = next(iter(topology["root"].values()))
    return any(c.get("fault") == "unplugged" for c in bench["children"].values())


def _instrument_view(instrument: Instrument) -> dict[str, Any]:
    case = resolve_case(instrument.case)
    spec = catalogue().get(instrument.case)
    view = {
        "address": instrument.address,
        "case": instrument.case,
        "replacement_usd": spec.replacement_usd if spec else None,
        "datasheet": _read_datasheet(case),
    }
    if instrument.drives is not None:
        view["drives"] = instrument.drives
    else:
        view["probe"] = instrument.probe
    return view


def _load_card_sim(loaded: LoadedTask, state: RunState, store: RunStore, run_id: str) -> CardSim:
    """issue #313: the card as `CardSim` sees it for THIS run — restored from
    whatever a previous `drive_input` call on this run id already applied
    (``state.card_applied`` / ``state.card_destroyed``), never recomputed
    from scratch each call. Logs to this run's own sim log (`RunStore.
    sim_log_path`), the same file `take_measurement` writes to, so a
    protection/damage line sits right next to the player's own SCPI
    exchanges."""
    doc = yaml.safe_load(Path(loaded.card_path).read_text(encoding="utf-8"))
    card_sim = CardSim(doc, log_path=store.sim_log_path(run_id))
    if state.card_applied:
        card_sim.applied = dict(state.card_applied)
    card_sim.destroyed = state.card_destroyed
    return card_sim


def drive_input(run_id: str, address: str, volts: float, *,
                state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Apply ``volts`` to the card input the instrument at ``address``
    drives (issue #313 Agent path): the one place `CardSim.state` /
    `CardSim.apply_input` are reachable by an agent at all, through the
    runner and `shal-arena drive` (CTO review on #323 — until this, neither
    was used outside the sim's own unit tests, and the card never had any
    state an agent's actions could change).

    Protection and damage here are a consequence of the player's own
    'drives' instrument, independent of the run's hidden fault — this never
    reads or reveals it (DoD 4)."""
    store = RunStore(state_dir)
    # one call that reaches the sim is one turn; also refuses a closed run
    # before anything is applied (issue #325).
    state = store.increment_turns(run_id)
    loaded = load_task(state.task_path)
    instrument = next((i for i in loaded.task.instruments
                       if str(i.address) == str(address)), None)
    if instrument is None:
        known = ", ".join(str(i.address) for i in loaded.task.instruments)
        raise CheckCouldNotRun(f"no instrument at address {address!r} on run {run_id!r}",
                               fix=f"use one of this run's addresses: {known}")
    if instrument.drives is None:
        raise CheckCouldNotRun(
            f"{address}: this instrument probes the card, it does not drive an input",
            fix="drive the address whose task.instruments entry has 'drives', not 'probe'")
    input_name = instrument.drives.removeprefix("card.")

    card_sim = _load_card_sim(loaded, state, store, run_id)
    SimLog(store.sim_log_path(run_id)).log_command(str(address), f"VOLT {volts}")
    result = card_sim.apply_input(input_name, volts, address=str(address))
    store.set_card_state(run_id, applied=card_sim.applied, destroyed=card_sim.destroyed)
    return {"run_id": run_id, "address": instrument.address, **result.as_dict()}


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
    against it. Lights the address's tile when the report has no problems.

    issue #312: the topology it binds to is THIS run's (`_topology_for_
    instrument` — the static harness, or an in-memory fault-wired one), so a
    correct driver is validated against what is actually there. Nothing
    about this check is logged to the sim log — CTO review on #322: that
    would make this *structural* check double as crediting a measurement
    with no action from the player (every player runs it, pass or fail, just
    to light the tile). `take_measurement` is the player's own read."""
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
    try:
        report = _conformance_check_driver(case.compatible, topology=topology)
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


def _zero_arg_read_op(cls: type) -> str | None:
    from shal.driver import inferred_side_effect

    return next((name for name, fn in cls.capability_ops().items()
                if inferred_side_effect(fn) == "none"
                and not (getattr(fn, "__shal_op__", {}) or {}).get("params")), None)


def take_measurement(run_id: str, address: str, driver_path: str | Path, *,
                     state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """The player's own deliberate measurement (issue #312; CTO review on
    #322: "the log must record the player's own reads, through their bound
    HAL or MCP session"). Imports ``driver_path``, binds it to THIS run's
    harness (`_topology_for_instrument` — the static one, or the in-memory,
    fault-wired one), and calls its one zero-argument read op through that
    bound driver, exactly once.

    Right before that call, a neutral ``measure`` marker is written to the
    sim log (``SimLog.mark_measured``) — same shape whatever happens next,
    so its presence is what `answer`'s disqualification check counts (CTO
    review on #322, round 2: checking for a successful ``query`` instead
    meant the `open` fault, unreachable by construction, could never be
    logged as measured, so a correctly-reasoned `open` answer was always
    disqualified). A successful exchange also lands its own ``query`` in the
    log, same as always. A failed one (the instrument is unreachable — the
    `open` fault, extending `fault: unplugged` — or a bug in the driver)
    raises `MeasurementFailed` with whatever the real exception says: a live
    answer to THIS call, reported to whoever just made it, never written to
    the sim log or any other file beyond that one neutral marker (Scope:
    "never written to a file the player or agent can read" — a marker that
    is identical for every outcome names nothing)."""
    from shal import registry
    from shal.hal import load as _load

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
    try:
        cls = registry.resolve(case.compatible)
    except Exception as e:  # noqa: BLE001 - a bad/missing registration is a named failure
        raise CheckCouldNotRun(
            f"driver.py does not register {case.compatible!r}: {type(e).__name__}: {e}",
            fix=f"make sure your driver.py sets compatible = {case.compatible!r} and "
                "calls registry.register(...)") from e
    read_op = _zero_arg_read_op(cls)
    if read_op is None:
        raise CheckCouldNotRun(
            f"{case.compatible}: no zero-argument read op to measure with",
            fix="this case has no op with side_effect='none' and no required "
                "params — measuring it needs a different driver shape")

    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case)
    card_sim = _load_card_sim(loaded, state, store, run_id)
    rail = (next((r for r in loaded.card.rails
                  if instrument.probe == f"card.{r.test_point}"), None)
            if instrument.probe is not None else None)
    if (rail is not None and rail.nominal_v != 0 and card_sim.rail_voltage(rail.name) == 0.0
            and not (isinstance(topology, dict) and _fault_is_unplugged(topology))):
        # the card is in protection or destroyed: the rail reads dead, not the
        # healthy fixed value (issue #325).
        topology = _fault.harness_for_run(
            case, rail=rail, seed=state.seed,
            realized=_fault.RealizedFault("dead", {"shift_v": -rail.nominal_v}))
    sim_log = SimLog(store.sim_log_path(run_id))
    try:
        with sim_log.record_for(str(address)), _load(topology) as hal:
            node = next((n for root in hal._roots for n in root.walk()
                        if isinstance(n.driver, cls)), None)
            if node is None:
                raise CheckCouldNotRun(
                    f"{case.name}: its own harness binds no node to {case.compatible!r}",
                    fix="this is a packaged case's harness, not your driver.py — "
                        "if you see this, file a shal-arena issue")
            sim_log.mark_measured(str(address))
            reading = getattr(node.driver, read_op)()
    except CheckCouldNotRun:
        raise
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call, never persisted
        raise MeasurementFailed(
            f"{read_op} raised {type(e).__name__}: {e}",
            fix="the instrument did not answer this call — if that's unexpected, "
                "check your driver.py's handling of the case's SCPI dialect") from e
    card = {"state": card_sim.state, "applied": dict(card_sim.applied),
            "supply_a": card_sim.supply_current()}
    result = {
        "ok": True,
        "side_effect": "write",
        "run_id": run_id,
        "address": instrument.address,
        "case": instrument.case,
        "op": read_op,
        "reading": reading,
        "card": card,
    }
    spec = catalogue().get(instrument.case)
    if spec is not None and spec.fuse_a is not None:
        # a DMM's current input sits in the card's supply path: a burned fuse
        # reads 0 A, a destroyed card draws what the damage model says.
        dmm = Dmm(spec, log_path=store.sim_log_path(run_id), address=str(address))
        dmm.fuse_blown = str(address) in state.fuses_blown
        result["current_a"] = dmm.measure_current(card_sim.supply_current())
        result["fuse"] = dmm.state
        if dmm.fuse_blown and str(address) not in state.fuses_blown:
            store.set_fuse_blown(run_id, str(address))
    return result


def answer(run_id: str, value: str, *, state_dir: str | Path = DEFAULT_STATE_DIR
          ) -> dict[str, Any]:
    """Close the run: compare ``value`` against the hidden fault and write the
    record (issue #310 Scope: "takes the answer; writes the run record").

    issue #312 adds: ``disqualified`` (true when the sim log has no ``measure``
    marker at any probe instrument's address: the player never called
    `take_measurement` for one — regardless of whether that call's read then
    succeeded) and ``score`` (the 13-field score file, also written to
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
        sim_log.has_measure(addr) for addr in probe_addresses)
    score = build_score(task_id=loaded.task.id, seed=state.seed, fault_id=fault_id,
                        given=value, correct=record["correct"], disqualified=disqualified,
                        created_at=state.created_at, closed_at=record["closed_at"],
                        turns=state.turns, record_path=store.record_path(run_id))
    store.write_score(run_id, score)
    return {"ok": True, "side_effect": "write", **record,
            "disqualified": disqualified, "score": score, "sim_log": str(sim_log.path)}
