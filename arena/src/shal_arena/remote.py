"""The server side of ``--server`` (issue #486): a sim bus the agent's driver
talks to over the wire, and nothing else.

``shal-arena serve`` owns the seed, the simulator and the run state. A
``measure`` or ``call`` from a client opens a *session* here (``begin``): one
turn, the run's own fault-wired sim bus built exactly as a local run builds
it, kept in memory. The client's driver then makes ``exchange`` (SCPI and
Modbus messages) and ``txn`` (I2C bytes, hex) requests against that bus, and
``finish`` closes the session. The bus is the only thing a client reaches --
no route here takes code, a path or a module name, and the agent's
``driver.py`` is never imported in this process.

Scoring reads this side's traffic only. The ``measure`` marker that
``runner.answer`` counts is written when the first exchange for a *probe*
instrument arrives (``_mark``), before the sim answers, so what counts as
"measured something" is real bus traffic. The reading a client reports in
``finish`` is shown in the sim log and the result and never scored.
"""
from __future__ import annotations

import math
import secrets
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shal.transport import ByteTransport, MessageTransport, Op, Read, Write

from . import fault as _fault
from .card_sim import CardSim, Dmm, catalogue
from .cases import CaseSpec, resolve_case
from .errors import ArenaError, CheckCouldNotRun
from .loader import load_task
from .runner import (
    _dead_rail_override,
    _load_card_sim,
    _make_card_state,
    _pin_probe,
    _sim_bus_for_topology,
    _topology_for_instrument,
    run_fault_ids,
)
from .schema import Instrument
from .simlog import SimLog
from .store import RunState, RunStore

#: the one size limit: a request body over it is a 413 naming this number.
MAX_PAYLOAD_BYTES = 64 * 1024

ACTIONS = ("measure", "call")


@dataclass
class _Session:
    session_id: str
    run_id: str
    action: str
    address: str
    instrument: Instrument
    case: CaseSpec
    point: str | None
    label: str | None
    state: RunState
    card_sim: CardSim
    bus: Any
    child_addr: Any
    measured: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def _bus_error(e: Exception) -> dict[str, Any]:
    return {"type": type(e).__name__, "message": str(e),
            "delivered": getattr(e, "delivered", "unknown")}


class RemoteSessions:
    def __init__(self, state_dir: str | Path) -> None:
        self.store = RunStore(state_dir)
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.Lock()

    def get(self, run_id: str, session_id: Any) -> _Session:
        with self._lock:
            sess = self._sessions.get(session_id) if isinstance(session_id, str) else None
        if sess is None or sess.run_id != run_id:
            raise ArenaError("remote: no such open session on this run",
                             fix="open one with POST /api/remote/begin first")
        return sess

    def begin(self, run_id: str, action: str, address: str, at: str | None) -> dict[str, Any]:
        """One command that reaches the sim is one turn (counted first, as
        the local runner does). Returns what a client needs to bind its own
        driver to the remote bus: the case's compatible and the sim child's
        address -- never the topology's config (the fault)."""
        if action not in ACTIONS:
            raise ArenaError(f"remote: unknown action {action!r}",
                             fix=f"use one of: {', '.join(ACTIONS)}")
        state = self.store.increment_turns(run_id)
        loaded = load_task(state.task_path)
        instrument = next((i for i in loaded.task.instruments
                           if str(i.address) == str(address)), None)
        if instrument is None:
            known = ", ".join(str(i.address) for i in loaded.task.instruments)
            raise CheckCouldNotRun(f"no instrument at address {address!r} on run {run_id!r}",
                                   fix=f"use one of this run's addresses: {known}")
        case = resolve_case(instrument.case)
        instrument, point, label = _pin_probe(loaded.card, instrument, at)
        topology = _topology_for_instrument(loaded.task, loaded.card, instrument, state.seed,
                                            case, nonce=state.turns,
                                            fault_ids=run_fault_ids(loaded.card, state))
        card_sim = _load_card_sim(loaded, state, self.store, run_id)
        topology = _dead_rail_override(topology, loaded, instrument, case, card_sim, state.seed)
        bus, child_addr = _sim_bus_for_topology(topology)
        model = bus.model_for(child_addr)
        if instrument.probe is not None:
            realized = _fault.realized_fault(loaded.card, state.seed,
                                             run_fault_ids(loaded.card, state))
            card_state = _make_card_state(loaded.card, card_sim, realized)
            bound = instrument.probe.removeprefix("card.")
            bind = getattr(model, "bind_card_state", None)
            if bind is not None:
                bind(lambda: card_state(bound))
        if case.power_switch:
            _seed_card_power_model(model, card_sim)
        sess = _Session(session_id=_new_id(), run_id=run_id, action=action,
                        address=str(instrument.address), instrument=instrument, case=case,
                        point=point, label=label, state=state, card_sim=card_sim, bus=bus,
                        child_addr=child_addr)
        with self._lock:
            self._sessions[sess.session_id] = sess
        return {"ok": True, "side_effect": "write", "run_id": run_id, "session": sess.session_id,
                "address": sess.address, "case": instrument.case, "compatible": case.compatible,
                "child_address": child_addr, "drives": instrument.drives is not None,
                "power_switch": case.power_switch, "point": point, "label": label}

    def _mark(self, sess: _Session) -> None:
        """The neutral ``measure`` marker, once per session, on the first bus
        request that reaches a probe instrument -- before the sim answers,
        whatever it then does (so an unplugged or `open` instrument still
        counts: the traffic happened)."""
        with sess.lock:
            if sess.measured or sess.instrument.probe is None:
                return
            sess.measured = True
            SimLog(self.store.sim_log_path(sess.run_id)).mark_measured(
                sess.address, point=sess.point, label=sess.label)

    def _talk(self, sess: _Session, address: Any, call) -> dict[str, Any]:
        if str(address) != str(sess.child_addr):
            raise ArenaError(f"remote: this session's bus has one device, at {sess.child_addr!r}",
                             fix=f"send address {sess.child_addr!r}")
        self._mark(sess)
        sim_log = SimLog(self.store.sim_log_path(sess.run_id))
        try:
            with sim_log.record_for(sess.address):
                return {"ok": True, "side_effect": "write", **call()}
        except Exception as e:  # noqa: BLE001 - a bus failure is a live answer to the client
            return {"ok": False, "side_effect": "write", "bus_error": _bus_error(e)}

    def exchange(self, run_id: str, session_id: Any, address: Any, message: Any) -> dict[str, Any]:
        sess = self.get(run_id, session_id)
        if not isinstance(message, dict) or not isinstance(sess.bus, MessageTransport):
            raise ArenaError("remote: exchange takes a JSON object message for a message bus",
                             fix="use /api/remote/txn with hex bytes for an I2C instrument")
        return self._talk(sess, address, lambda: {
            "reply": dict(sess.bus.exchange(sess.child_addr, message))})

    def txn(self, run_id: str, session_id: Any, address: Any, ops: Any) -> dict[str, Any]:
        sess = self.get(run_id, session_id)
        if not isinstance(sess.bus, ByteTransport):
            raise ArenaError("remote: txn is for a byte (I2C) bus",
                             fix="use /api/remote/exchange with a JSON message")
        parsed = _parse_ops(ops)
        return self._talk(sess, address, lambda: {
            "hex": sess.bus.txn(sess.child_addr, parsed).hex()})

    def finish(self, run_id: str, session_id: Any, body: dict[str, Any]) -> dict[str, Any]:
        sess = self.get(run_id, session_id)
        with self._lock:
            self._sessions.pop(sess.session_id, None)
        sim_log = SimLog(self.store.sim_log_path(run_id))
        base = {"ok": True, "side_effect": "write", "run_id": run_id, "address": sess.address}
        if sess.action == "call":
            op = _text(body, "op")
            args = body.get("args", [])
            if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
                raise ArenaError("remote: 'args' must be a list of strings",
                                 fix='send "args" like ["0", "true"]')
            if sess.case.power_switch:  # keep the card's power in sync with the relay's coil
                coils = getattr(sess.bus.model_for(sess.child_addr), "coils", None)
                if coils is not None:
                    power_on = bool(coils.get(0, True))
                    sess.card_sim.set_power(power_on)
                    self.store.set_card_power(run_id, power_on)
            sim_log.append(sess.address, "call", op=op, args=args,
                           ok=bool(body.get("ok", True)))
            return {**base, "op": op, "args": args}
        points = {"point": sess.point, "label": sess.label} if sess.point is not None else {}
        if body.get("failed"):
            cause = "transport" if body.get("cause") == "transport" else "driver"
            sim_log.append(sess.address, "failed", cause=cause, **points)
            return base
        reading = body.get("reading")
        if isinstance(reading, bool) or not isinstance(reading, (int, float)) \
                or not math.isfinite(reading):
            raise ArenaError("remote: 'reading' must be a finite number",
                             fix="finish a measure with the number your driver read")
        unit = body.get("unit")
        if unit is not None and not (isinstance(unit, str) and len(unit) <= 32):
            raise ArenaError("remote: 'unit' must be a short string or null",
                             fix="send the op's unit, e.g. \"volt\"")
        sim_log.mark_reading(sess.address, reading, unit, point=sess.point, label=sess.label)
        return self._measure_result(sess, base, _text(body, "op"), float(reading), unit)

    def _measure_result(self, sess: _Session, base: dict[str, Any], op: str, reading: float,
                        unit: str | None) -> dict[str, Any]:
        """The fields a local ``measure`` returns, from this side's own state."""
        card_sim, instrument, address = sess.card_sim, sess.instrument, sess.address
        result = {**base, "case": instrument.case, "op": op, "reading": reading,
                  "card": {"state": card_sim.state, "applied": dict(card_sim.applied),
                           "supply_a": card_sim.supply_current()}}
        if instrument.probe is not None:
            probed = instrument.probe.removeprefix("card.")
            self.store.add_reading(sess.run_id, address, point=probed, value=reading, unit=unit)
            result["point"] = probed
            if sess.label is not None:
                result["label"] = sess.label
        spec = catalogue().get(instrument.case)
        if spec is not None and spec.fuse_a is not None:
            dmm = Dmm(spec, log_path=self.store.sim_log_path(sess.run_id), address=address)
            dmm.fuse_blown = address in sess.state.fuses_blown
            result["current_a"] = dmm.measure_current(card_sim.supply_current())
            result["fuse"] = dmm.state
            if dmm.fuse_blown and address not in sess.state.fuses_blown:
                self.store.set_fuse_blown(sess.run_id, address)
        return result


def _seed_card_power_model(model: Any, card_sim: CardSim) -> None:
    """`runner._seed_card_power`, for a model already in hand."""
    coils = getattr(model, "coils", None)
    if coils is not None:
        coils[0] = card_sim.power_on


def _new_id() -> str:
    return secrets.token_hex(8)


def _text(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value or len(value) > 64:
        raise ArenaError(f"remote: {key!r} must be a short non-empty string",
                         fix=f"send {key!r} as the name your driver used")
    return value


def _parse_ops(ops: Any) -> list[Op]:
    """Wire ops -> `Write`/`Read`: ``{"op": "write", "hex": "01ff"}`` and
    ``{"op": "read", "n": 2}``. I2C bytes are hex strings, never raw."""
    if not isinstance(ops, list) or not ops:
        raise ArenaError("remote: 'ops' must be a non-empty list",
                         fix='send ops like [{"op": "write", "hex": "00"}, {"op": "read", "n": 2}]')
    parsed: list[Op] = []
    for o in ops:
        kind = o.get("op") if isinstance(o, dict) else None
        if kind == "write" and isinstance(o.get("hex"), str):
            try:
                parsed.append(Write(bytes.fromhex(o["hex"])))
            except ValueError as e:
                raise ArenaError(f"remote: write 'hex' is not hex: {e}",
                                 fix="send bytes as a hex string, e.g. \"00ff\"") from e
        elif (kind == "read" and isinstance(o.get("n"), int) and not isinstance(o["n"], bool)
              and 0 < o["n"] <= MAX_PAYLOAD_BYTES):
            parsed.append(Read(o["n"]))
        else:
            raise ArenaError(f"remote: bad op {o!r}",
                             fix=f'use {{"op": "write", "hex": "00"}} or {{"op": "read", '
                                 f'"n": 1..{MAX_PAYLOAD_BYTES}}}')
    return parsed
