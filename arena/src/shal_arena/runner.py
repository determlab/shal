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

issue #314 adds `raw_scpi` (the "without SHAL" Agent path, for `bench`): no
driver.py, no gate, no record — a raw ``{"scpi": cmd, "query": bool}``
exchange with the same sim bus a player's driver would otherwise sit behind,
built by hand (`_sim_bus_for_topology`) rather than through `shal.hal.load`
(see that function's own docstring for why). Same seed, same topology, same
`CardSim` as the SHAL side, so the two sides play literally the same world.
"""
from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any

import shal
import yaml
from shal.buses.sim_scpi import SimScpiBus
from shal.conformance import check_driver as _conformance_check_driver
from shal.node import Node

from . import fault as _fault
from .card_sim import DAMAGE, CardSim, Dmm, catalogue
from .cases import CaseSpec, resolve_case
from .errors import ArenaError, CheckCouldNotRun, MeasurementFailed
from .loader import LoadedTask, load_task
from .schema import Card, Instrument, Task
from .score import build_score
from .simlog import SimLog
from .store import DEFAULT_STATE_DIR, RunState, RunStore

_AMBIENT_C = 25.0  # a card with no power dissipates none, so it cools to room temperature


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
                             seed: int, case: CaseSpec, *, nonce: int = 0) -> str | dict:
    """issue #312: the harness this instrument's `check` runs against for
    THIS run. An instrument with no `probe:` (it `drives:` a card input
    instead) never carries a fault — always the case's static harness.  A
    `probe:` instrument gets the static harness too UNLESS the seed's
    realized fault targets the exact rail its wiring names; only then does
    it get the in-memory, fault-wired topology from `fault.harness_for_run`
    (never written to disk — Scope: "never written to a file the player or
    agent can read").

    ``nonce`` (issue #431): forwarded to `fault.harness_for_run` unchanged —
    see its own docstring for why a fresh value per call matters for the
    `noise` fault."""
    static = str(case.harness_topology)
    if instrument.probe is None:
        return static
    realized = _fault.realized_fault(card, seed)
    rail = _fault.rail_for_fault(card, realized)
    if rail is None or instrument.probe != f"card.{rail.test_point}":
        return static
    return _fault.harness_for_run(case, rail=rail, realized=realized, seed=seed, nonce=nonce)


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
    elif instrument.switches is not None:  # issue #473: a relay, not a source
        view["switches"] = instrument.switches
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
    card_sim.power_on = state.card_power_on
    return card_sim


def _make_card_state(card: Card, card_sim: CardSim,
                     realized: _fault.RealizedFault) -> Callable[[str], float]:
    """issue relay-rail: the one live-value function every probing sim can be
    bound to (`_bind_card_state`), card-yaml driven so it needs no per-case
    knowledge of what kind of sensor is reading it. A rail's test point
    reads `CardSim.rail_voltage` (already relay/protection/damage aware); a
    temperature point reads the card's own nominal plus this run's realized
    fault shift, if the realized fault targets it -- unless the card's
    power is off (CTO review on PR #426), in which case nothing on it is
    dissipating any power any more and it reads room temperature instead,
    whatever fault is realized."""
    def card_state(point: str) -> float:
        rail = next((r for r in card.rails if r.test_point == point), None)
        if rail is not None:
            return card_sim.rail_voltage(rail.name)
        temp = next((t for t in card.temp_points if t.test_point == point), None)
        if temp is not None:
            if not card_sim.power_on:
                return _AMBIENT_C
            value = temp.nominal_c
            target = _fault.temp_point_for_fault(card, realized)
            if target is not None and target.test_point == point:
                value += realized.extra.get("shift_c", 0.0)
            return value
        known = sorted(r.test_point for r in card.rails) + \
            sorted(t.test_point for t in card.temp_points)
        raise ArenaError(f"no such card point {point!r}",
                         fix=f"probe one of this card's test points: {known}")
    return card_state


def _bind_card_state(node: Node, point: str, card_state: Callable[[str], float]) -> None:
    """issue relay-rail: bind this probe's live value, as a zero-argument
    callback, onto the sim model behind ``node`` — one bind step for any bus
    kind (``shal,sim-scpi``, ``shal,sim-i2c``, ``shal,sim-msg`` all expose
    ``model_for``), added HERE because shal core's sim-i2c bus has no
    ``bind_sim`` hook of its own (only sim-scpi does — this is the "bind
    step for sim-i2c" the CTO ruling asks the arena harness to add, not shal
    core). A model that defines no ``bind_card_state`` is left untouched."""
    bus = getattr(node.driver, "bus", None)
    model_for = getattr(bus, "model_for", None)
    if model_for is None:
        return
    try:
        model = model_for(node.address)
    except Exception:  # noqa: BLE001 - best-effort; a real read raises properly on its own
        return
    bind = getattr(model, "bind_card_state", None)
    if bind is not None:
        bind(lambda: card_state(point))


def _seed_card_power(node: Node, card_sim: CardSim) -> None:
    """issue relay-rail: the counterpart of `_sync_card_power` — a fresh
    `shal.load` builds a fresh relay model every call (channel 0 starting
    energized), so before the call reaches it, seed that channel from
    `CardSim.power_on` (itself restored from the store by `_load_card_sim`)
    so a `read_relay` after an earlier call's `set_relay` sees what was
    actually last set, not the model's own default."""
    bus = getattr(node.driver, "bus", None)
    model_for = getattr(bus, "model_for", None)
    if model_for is None:
        return
    model = model_for(node.address)
    coils = getattr(model, "coils", None)
    if coils is not None:
        coils[0] = card_sim.power_on


def _sync_card_power(node: Node, card_sim: CardSim, store: RunStore, run_id: str) -> None:
    """issue relay-rail: after any `call` on a `power_switch` case's
    instrument (`cases.CaseSpec.power_switch`), keep `CardSim.power_on` in
    sync with that instrument's own real state (e.g. the relay's channel 0
    coil) and persist it (`RunStore.set_card_power`) the same way
    `drive_input` persists `card_applied` (issue #313) — so a later call on
    a different address, in a later CLI invocation, sees the same card."""
    bus = getattr(node.driver, "bus", None)
    model_for = getattr(bus, "model_for", None)
    if model_for is None:
        return
    model = model_for(node.address)
    coils = getattr(model, "coils", None)
    if coils is None:
        return
    power_on = bool(coils.get(0, True))
    card_sim.set_power(power_on)
    store.set_card_power(run_id, power_on)


_BOOL_TRUE = {"true", "1", "yes", "on"}
_BOOL_FALSE = {"false", "0", "no", "off"}


def _coerce_one(raw: str, annotation: Any) -> Any:
    if annotation is bool:
        low = raw.strip().lower()
        if low in _BOOL_TRUE:
            return True
        if low in _BOOL_FALSE:
            return False
        raise CheckCouldNotRun(f"{raw!r} is not a boolean",
                               fix="pass true/false (or 1/0/yes/no/on/off)")
    if annotation is int:
        try:
            return int(raw)
        except ValueError:
            raise CheckCouldNotRun(f"{raw!r} is not an integer",
                                   fix="pass a whole number") from None
    if annotation is float:
        try:
            return float(raw)
        except ValueError:
            raise CheckCouldNotRun(f"{raw!r} is not a number", fix="pass a number") from None
    return raw


def _coerce_args(fn: Callable, args: list[str]) -> dict[str, Any]:
    """issue relay-rail: positional string CLI args -> a kwargs dict for
    ``op``'s own signature, coerced by its parameter annotations (bool/int/
    float; anything else passes through as `str`) — the generic `call`
    path works for any op of any driver without the CLI knowing its shape
    ahead of time. ``get_type_hints`` (not the raw ``Parameter.annotation``)
    because every packaged/example driver has ``from __future__ import
    annotations``, which makes annotations plain strings at runtime."""
    sig = inspect.signature(fn)
    params = [p for p in sig.parameters if p != "self"]
    if len(args) > len(params):
        raise CheckCouldNotRun(
            f"{fn.__name__} takes at most {len(params)} argument(s) {params}, got {len(args)}",
            fix=f"pass at most {len(params)} argument(s): {params}")
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # noqa: BLE001 - a hint that can't resolve just passes through as str
        hints = {}
    return {name: _coerce_one(raw, hints.get(name, sig.parameters[name].annotation))
           for name, raw in zip(params, args, strict=False)}


def _shal_damage_gate(card_sim: CardSim, input_name: str,
                      address: str) -> tuple[Any, dict[str, Any]]:
    """issue #338: the SHAL side's gate for `drive_input` is shal's own
    enforcement chain (limits.py, then approval.py, then I/O; driver.py), not
    a function written inside the arena — if shal's gate broke, this would
    show it breaking too. Driven by the card's own documented absolute
    maximum for ``input_name`` (a `damage`/`destroyed` limit with a `source`
    in the card yaml): one real `shal,sim-psu` node, bound through
    `shal.load`, whose `config.limits` narrows `set_voltage`'s advertised
    0-30 V range down to that one number — the SAME mechanism a real
    installation uses to tighten a driver's class-level bounds
    (`src/shal/limits.py` "installation policy" layer). `shal.AutoApprove()`
    is seated so a refusal here can only be the limit, never an approval deny
    (protection-only and in-range actions pass; `raw_scpi` has no gate).

    If this card's documented damage limit for ``input_name`` cannot be
    expressed this way (no such limit on the card), the caller's own
    `NotSupported` tells the operator to flag it rather than invent a second,
    arena-local gate (issue #338 Constraints)."""
    candidates = [lim for lim in card_sim.limits
                 if lim.input == input_name and lim.effect == DAMAGE and lim.documented]
    # CTO review on #338: a community card could declare more than one
    # DAMAGE-effect line for the same input; the gate must be the LOWEST one
    # (the first one actually destroyed), not whichever happens to come first
    # in file order.
    limit = min(candidates, key=lambda lim: lim.above_v) if candidates else None
    if limit is None:
        raise NotSupported(
            f"{input_name}: this card declares no documented damage limit for this "
            "input, so there is nothing for shal's own gate to enforce",
            fix="label this agent:needs-human; do not add a second, arena-local gate")
    topology = {
        "shal_version": 1,
        "root": {
            "bench": {
                "driver": "shal,sim-scpi",
                "address": f"sim-gate-{address}",
                "children": {
                    "psu": {
                        "id": "arena_gate_psu",
                        "driver": "shal,sim-psu",
                        "address": f"gate-{address}",
                        "config": {"limits": {
                            "set_voltage": {"volts": {"maximum": limit.above_v}}}},
                    },
                },
            },
        },
    }
    hal = shal.load(topology, approver=shal.AutoApprove())
    detail: dict[str, Any] = {}

    def gate(action: dict[str, Any]) -> bool:
        result = hal.call_tool("arena_gate_psu__set_voltage", {"volts": action["volts"]})
        if result["ok"]:
            return True
        detail["rejected"] = result.get("rejected")
        detail["error"] = result.get("error")
        detail["violations"] = result.get("violations")
        return False

    return gate, detail


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
    if instrument.switches is not None:
        # issue #473: a `switches` instrument (relay0) only turns an input on
        # or off -- its one action goes through `call`, never `drive`.
        raise CheckCouldNotRun(
            f"{address}: this instrument switches {instrument.switches} on/off, "
            "it does not drive an input",
            fix=f"use `shal-arena call <run> {address} <driver.py> set_relay "
                "<channel> <true|false>` instead of drive")
    if instrument.drives is None:
        raise CheckCouldNotRun(
            f"{address}: this instrument probes the card, it does not drive an input",
            fix="drive the address whose task.instruments entry has 'drives', not 'probe'")
    case = resolve_case(instrument.case)
    if case.power_switch:
        # CTO review (PR #426): a relay is not a voltage source -- `drive`
        # is CardSim.apply_input's own gate (_shal_damage_gate below), which
        # a power switch has no business reaching; its one real action
        # (energize/de-energize) goes only through `call`'s own gate.
        raise CheckCouldNotRun(
            f"{address}: this is a power switch, not a voltage source",
            fix=f"use `shal-arena call <run> {address} <driver.py> set_relay "
                "<channel> <true|false>` instead of drive")
    input_name = instrument.drives.removeprefix("card.")

    card_sim = _load_card_sim(loaded, state, store, run_id)
    gate, gate_detail = _shal_damage_gate(card_sim, input_name, str(address))
    result = card_sim.apply_input(input_name, volts, gate=gate, address=str(address))
    store.set_card_state(run_id, applied=card_sim.applied, destroyed=card_sim.destroyed)
    # side_effect stays "write" from as_dict() even on a refusal: it counted a
    # turn and wrote a refused line to the sim log (CTO review on #330,
    # following the #328 ruling for apply_input itself).
    out = {"run_id": run_id, "address": instrument.address, **result.as_dict()}
    if not result.sent:  # shal's own gate stopped it: nothing was applied (issue #338)
        out["rejected"] = gate_detail.get("rejected", out.get("rejected"))
        if gate_detail.get("violations") is not None:
            out["violations"] = gate_detail["violations"]
        out["reason"] = (f"{volts} V on {input_name} would damage the card; "
                         f"shal's own gate refused it ({gate_detail.get('error')}) "
                         "and nothing was sent")
        out["fix"] = "pick a voltage inside the card's documented input range"
    return out


# "VOLT <value>" (scpi-psu's own datasheet command for a setpoint write) is the
# one write syntax the packaged catalogue's "drives" case uses today — the
# same regex the harness's own reference model (adk/scpi-psu/harness/sim.py)
# and shal core's own shal,sim-psu model parse, so a without-SHAL player who
# read the datasheet sends exactly what those parse.
_RAW_SET_V = re.compile(r"^VOLT\s+([0-9.eE+-]+)$")


def _sim_bus_for_topology(topology: str | dict) -> tuple[SimScpiBus, Any]:
    """issue #314: the "without SHAL" side's socket onto THIS run's sim —
    same topology (static, or in-memory fault-wired) `_topology_for_instrument`
    hands the SHAL side, built by hand instead of through `shal.hal.load`.

    `shal.hal.load` binds every child's `driver:` compatible through the
    driver registry (`registry.resolve`), which needs a REGISTERED `Driver`
    class — exactly what a player's driver.py supplies on the SHAL side, and
    exactly what "no drivers" (Scope) means there is none of here. A
    `shal,sim-scpi` bus only reads each child's bare `address`/`spec` to pick
    its sim model (`SimScpiBus.activate`), so a minimal, un-bound `Node` pair
    is enough to exchange with it directly — the same ``{"scpi": cmd,
    "query": bool} -> {"reply": text}`` contract a real ``shal,scpi-raw``
    link uses (sim_scpi.py's own docstring), returned raw rather than wrapped
    in any typed op."""
    doc = (yaml.safe_load(Path(topology).read_text(encoding="utf-8"))
          if isinstance(topology, (str, Path)) else topology)
    bench_name, bench_spec = next(iter(doc["root"].items()))
    bench = Node(bench_name, address=bench_spec.get("address"), id=bench_spec.get("id"))
    bench.spec = bench_spec
    child_name, child_spec = next(iter(bench_spec["children"].items()))
    child = Node(child_name, address=child_spec.get("address"), id=child_spec.get("id"),
                parent=bench)
    child.spec = child_spec
    bench.children[child_name] = child
    return SimScpiBus(bench), child.address


def raw_scpi(run_id: str, address: str, cmd: str, *,
            state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """The "without SHAL" Agent path (issue #314 Scope: "the agent gets raw
    SCPI access (socket-like) to the same sim. No drivers, no gate, no
    record"). One call is one turn, counted before anything runs — same rule,
    same place as `check_instrument_driver`/`take_measurement`/`drive_input`
    (issue #325): a bad command still costs its turn.

    A "drives" instrument's write goes straight into `CardSim.apply_input`,
    never the bus — the SAME physical act `drive_input` performs on the SHAL
    side (see its own docstring: it never reaches the bus either), so
    identical commands apply identical voltages on both sides. A "probe"
    instrument's command is a real exchange with this run's own sim bus
    (`_sim_bus_for_topology`), captured by the SAME `SimLog` `take_measurement`
    uses — captured below the player's surface (simlog.py: "nothing the
    player does ... can change its shape"), so the two sides' sim logs share
    one format by construction, not by two implementations trying to agree."""
    store = RunStore(state_dir)
    state = store.increment_turns(run_id)
    loaded = load_task(state.task_path)
    instrument = next((i for i in loaded.task.instruments
                       if str(i.address) == str(address)), None)
    if instrument is None:
        known = ", ".join(str(i.address) for i in loaded.task.instruments)
        raise CheckCouldNotRun(f"no instrument at address {address!r} on run {run_id!r}",
                               fix=f"use one of this run's addresses: {known}")
    case = resolve_case(instrument.case)

    # issue #473: a `switches` instrument keeps the raw path it had as a
    # `drives` one -- only the key changed, not the behaviour.
    wired_input = instrument.drives if instrument.drives is not None else instrument.switches
    if wired_input is not None:
        m = _RAW_SET_V.match(cmd.strip())
        if not m:
            raise CheckCouldNotRun(
                f"{address}: {cmd!r} is not a command this instrument's datasheet "
                "documents for driving an input",
                fix=f"read {case.docs_dir}/datasheet.md for the write command that "
                    "sets the output voltage, e.g. 'VOLT 5.0'")
        input_name = wired_input.removeprefix("card.")
        card_sim = _load_card_sim(loaded, state, store, run_id)
        result = card_sim.apply_input(input_name, float(m.group(1)), address=str(address))
        store.set_card_state(run_id, applied=card_sim.applied, destroyed=card_sim.destroyed)
        return {"run_id": run_id, "address": instrument.address, "cmd": cmd, **result.as_dict()}

    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case,
                                        nonce=state.turns)
    card_sim = _load_card_sim(loaded, state, store, run_id)
    topology = _dead_rail_override(topology, loaded, instrument, case, card_sim, state.seed)
    sim_log = SimLog(store.sim_log_path(run_id))
    is_query = cmd.strip().endswith("?")
    try:
        with sim_log.record_for(str(address)):
            # the same neutral marker `take_measurement` writes, BEFORE the
            # exchange and regardless of its outcome (CTO review on #328): a
            # probe instrument's own disqualification rule (`answer`, "no
            # measure marker at any probe address") must read identically on
            # both sides, or a without-SHAL run is disqualified even when
            # every command it sent was correct.
            sim_log.mark_measured(str(address))
            bus, child_addr = _sim_bus_for_topology(topology)
            reply = bus.exchange(child_addr, {"scpi": cmd, "query": is_query})
            if is_query:
                try:
                    sim_log.mark_reading(str(address), float(reply["reply"]), None)
                except (TypeError, ValueError):
                    pass  # not a bare number -- nothing to show as a reading
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call, never persisted
        raise MeasurementFailed(
            f"{cmd!r} raised {type(e).__name__}: {e}",
            fix="the instrument did not answer this raw command — if that's "
                "unexpected, check the datasheet's command syntax") from e
    # "write" like every other runner call (check/measure/drive): each one
    # mutates the RUN's own record (a turn, a sim log line) regardless of
    # whether the underlying instrument op itself only reads (CTO review on
    # #328 — a query still costs a turn and writes the sim log, so it is
    # never side-effect-free at the run level).
    return {"ok": True, "side_effect": "write",
           "run_id": run_id, "address": instrument.address, "cmd": cmd,
           "reply": reply["reply"]}


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
            # issue #461: the question text itself no longer names which
            # point to measure or which limit to check -- the possible
            # answers (the task yaml's own `question.answer.values`) stay
            # visible here, machine-readable, same as `rails`/`temp_points`
            # below (#451's precedent): an agent reads the choices from
            # data, never by guessing from prose. `values` is `[]` for a
            # `kind: number` task (none ship today) -- `unit`/`tol` live on
            # `task.question.answer` too but aren't surfaced here, since no
            # task needs them yet; add them here if one does.
            "answer": {"kind": task.question.answer.kind,
                      "values": list(task.question.answer.values)},
        },
        "instruments": [_instrument_view(i) for i in task.instruments],
        # issue #428: the card's own documented limit, in volts -- without
        # this, the agent has no numeric tolerance to check a reading
        # against and must guess one (CTO review on #427: a real run judged
        # "2.9 V is outside 3.3 V +/-5-10%" against a limit it invented).
        "rails": [
            {"name": r.name, "test_point": r.test_point, "nominal_v": r.nominal_v,
             "tol_pct": r.tol_pct,
             # issue #451: unrounded gives 3.2009999999999996 (float
             # arithmetic on a percentage) -- 4 decimals is plenty of
             # precision for a volt-scale limit and reads like a number a
             # person wrote, not a float artifact.
             "min_v": round(r.nominal_v * (1 - r.tol_pct / 100), 4),
             "max_v": round(r.nominal_v * (1 + r.tol_pct / 100), 4)}
            for r in card.rails
        ],
        # issue #451: the same machine-readable gap as `rails`, for a card's
        # temperature limit (e.g. relay-rail's regulator, high_c: 85) --
        # today only text in the card description.
        "temp_points": [
            {"name": t.name, "test_point": t.test_point, "nominal_c": t.nominal_c,
             "high_c": t.high_c}
            for t in card.temp_points
        ],
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
    # issue #436 CTO review: `store.load` again here, after
    # `increment_turns` already returned the post-increment state, re-read
    # OUTSIDE that call's own lock -- a second process's write could land
    # in between, so this use the RETURNED state directly (same as
    # drive_input/call_op already do).
    state = store.increment_turns(run_id)
    loaded = load_task(state.task_path)
    instrument = next((i for i in loaded.task.instruments
                       if str(i.address) == str(address)), None)
    if instrument is None:
        known = ", ".join(str(i.address) for i in loaded.task.instruments)
        raise CheckCouldNotRun(f"no instrument at address {address!r} on run {run_id!r}",
                               fix=f"use one of this run's addresses: {known}")
    case = resolve_case(instrument.case)
    _import_driver_file(driver_path)
    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case,
                                        nonce=state.turns)
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


def _dead_rail_override(topology: str | dict, loaded: LoadedTask, instrument: Instrument,
                        case: CaseSpec, card_sim: CardSim, seed: int) -> str | dict:
    """issue #325: a rail in protection or destroyed must read dead, not its
    healthy fixed value — on EITHER access path (`take_measurement`'s typed
    read, `raw_scpi`'s raw one; issue #314), so this is the one place that
    decides it. Never written to disk: the override topology is in-memory
    only, same discipline as `fault.harness_for_run` itself."""
    rail = (next((r for r in loaded.card.rails
                 if instrument.probe == f"card.{r.test_point}"), None)
           if instrument.probe is not None else None)
    if (rail is not None and rail.nominal_v != 0 and card_sim.rail_voltage(rail.name) == 0.0
            and not (isinstance(topology, dict) and _fault_is_unplugged(topology))):
        return _fault.harness_for_run(
            case, rail=rail, seed=seed,
            realized=_fault.RealizedFault("dead", {"shift_v": -rail.nominal_v}))
    return topology


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
    # issue #436 CTO review: use the state `increment_turns` itself
    # returns -- a second `store.load` here would re-read OUTSIDE that
    # call's own lock, same bug as check_instrument_driver had.
    state = store.increment_turns(run_id)
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

    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case,
                                        nonce=state.turns)
    card_sim = _load_card_sim(loaded, state, store, run_id)
    topology = _dead_rail_override(topology, loaded, instrument, case, card_sim, state.seed)
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
            if instrument.probe is not None:
                point = instrument.probe.removeprefix("card.")
                realized = _fault.realized_fault(loaded.card, state.seed)
                card_state = _make_card_state(loaded.card, card_sim, realized)
                _bind_card_state(node, point, card_state)
            sim_log.mark_measured(str(address))
            reading = getattr(node.driver, read_op)()
            op_fn = cls.capability_ops()[read_op]
            unit = (getattr(op_fn, "__shal_op__", {}) or {}).get("unit")
            sim_log.mark_reading(str(address), reading, unit)
    except CheckCouldNotRun:
        raise
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call, never persisted
        # issue #432: the WATCH sentence used to infer a failed read's
        # cause from the log's own shape (a `query` line present or not) --
        # unreliable in practice. Log the real cause instead: `HopError`/
        # `HopTimeout` are shal core's own "the hop never completed" (the
        # `open` fault, extending `fault: unplugged`, is exactly this); any
        # other exception is the driver's own code raising, after or
        # without ever reaching the bus.
        cause = "transport" if isinstance(e, (shal.errors.HopError, shal.errors.HopTimeout)) \
            else "driver"
        sim_log.append(str(address), "failed", cause=cause)
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


def call_op(run_id: str, address: str, driver_path: str | Path, op_name: str,
           args: list[str] | None = None, *,
           state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """``shal-arena call`` (issue relay-rail): run ANY op of the player's own
    driver through SHAL's normal dispatch — `hal.call_tool`'s gate, limits
    and approval (AGENTS.md), exactly as `shal call` enforces them, not a
    second arena-local mechanism — rather than the fixed probe/drives pair
    `measure`/`drive` cover. This is how `relay0`'s coil ops are played:
    `call <run> relay0 <driver.py> set_relay 0 false`, `call <run> relay0
    <driver.py> read_relay 0`.

    One call is one turn, counted before anything runs, same place as
    check/measure/drive (issue #325); the exchange lands in this run's own
    sim log (`SimLog.append`), and a probe instrument called this way also
    gets the same `measure` marker `take_measurement` writes, so answering
    about it is never disqualified just because the reading came through
    this path instead of `measure`."""
    from shal import registry
    from shal.driver import inferred_side_effect
    from shal.hal import load as _load

    args = list(args or [])
    store = RunStore(state_dir)
    state = store.increment_turns(run_id)
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
    ops = cls.capability_ops()
    if op_name not in ops:
        known = ", ".join(sorted(ops)) or "(none)"
        raise CheckCouldNotRun(f"{case.compatible} has no op {op_name!r}",
                               fix=f"call one of this driver's ops: {known}")
    kwargs = _coerce_args(ops[op_name], args)
    side_effect = inferred_side_effect(ops[op_name])
    if instrument.drives is not None and not case.power_switch and side_effect != "none":
        # CTO review (PR #426): a `drives` instrument's own output change is
        # `CardSim.apply_input`'s gate to make (`drive_input`'s
        # `_shal_damage_gate`, the card's documented abs-max) -- `call`
        # reaching the bare sim model directly would let a player set an
        # output `drive` would have refused, with no damage check at all.
        # A power switch has no such gate to bypass (it never touches
        # `apply_input`), so it is exempt -- `call` is its only path.
        raise CheckCouldNotRun(
            f"{address}: {op_name} changes this instrument's own output; only "
            "`shal-arena drive` carries the card's damage gate for a 'drives' "
            "instrument",
            fix=f"use `shal-arena drive <run> {address} <volts>` instead of call")

    topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed, case,
                                        nonce=state.turns)
    card_sim = _load_card_sim(loaded, state, store, run_id)
    topology = _dead_rail_override(topology, loaded, instrument, case, card_sim, state.seed)
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
            if instrument.probe is not None:
                point = instrument.probe.removeprefix("card.")
                realized = _fault.realized_fault(loaded.card, state.seed)
                card_state = _make_card_state(loaded.card, card_sim, realized)
                _bind_card_state(node, point, card_state)
                sim_log.mark_measured(str(address))
            if case.power_switch:
                # a fresh `shal.load` means a fresh relay model every call
                # (same reason `_load_card_sim` restores `CardSim.applied`
                # from the store) — seed its channel 0 coil from the last
                # persisted power state before this call sees it.
                _seed_card_power(node, card_sim)
            # issue #457: `record_for` above is what gives this call's real
            # Modbus exchange ({fc, address, value}) its own `exchange` row
            # in the sim log -- captured at the bus layer (`sim_msg.py`),
            # not guessed here.
            result = hal.call_tool(f"{node.id or 'unit'}__{op_name}", kwargs)
            if case.power_switch:
                _sync_card_power(node, card_sim, store, run_id)
    except CheckCouldNotRun:
        raise
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call, never persisted
        raise MeasurementFailed(
            f"{op_name} raised {type(e).__name__}: {e}",
            fix="the call failed — check your driver.py's handling of this op") from e

    sim_log.append(str(address), "call", op=op_name, args=args, ok=bool(result.get("ok")))
    out = {"ok": bool(result.get("ok")), "side_effect": side_effect, "run_id": run_id,
          "address": instrument.address, "op": op_name, "args": args}
    out.update({k: v for k, v in result.items() if k != "ok"})
    return out
