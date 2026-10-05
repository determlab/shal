"""Generic card simulator + damage model (issue #313 Scope): "rails, test
points, limits, list of possible faults" come entirely from a ``card.yaml``
(``schema.Card``) — no card-specific Python, so a new card needs a new yaml
only, never new sim code (the same CTO-ruled format ``schema.py``/
``loader.py`` already validate for the buck-5v-3v3 example shipped with
issue #310). Before this ticket ``card.yaml``'s ``rails:``/``damage:`` were
descriptive only — ``fault.py``'s reading perturbation never looked at
either; this module is the first thing that actually runs that data as a
circuit.

Three damage states (issue #313 Scope): ``ok``, ``protection`` (OVP or fuse
tripped, instrument alive) and ``damage`` (destroyed for the rest of the
run — the rail dies). A state only ever escalates: once an input reaches
``damage`` nothing in this module ever clears it, and a later, lower
voltage never un-reports a past transition.

A threshold's ``source`` is checked HERE (``_source_of``), not only by
``schema.validate_card``'s required key — CTO ruling: "a limit with no
documented source is not counted as damage." ``DamageModel`` takes any
iterable of threshold-shaped objects, including a raw, unvalidated list of
dicts, so this rule holds even when nothing upstream already enforced it
(``test_damage.py``'s ``test_a_limit_with_no_source_never_causes_damage``).

The SHAL side (``SimCard``): one node simulates a whole card. Its one
actuator op, ``set_input_voltage`` (it energizes the simulated card now),
is gated like any other actuator (D4/D24, AGENTS.md) — SHAL's one gate stops
it, pre-I/O, before the voltage ever reaches ``CardSim``, so a denied call
causes no damage at all (issue #313 Scope: "with SHAL the gate stops the
action before damage"). Reads (``read_test_point``, ``state``) are free.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from shal import registry
from shal.driver import Driver, idempotent
from shal.driver import op as _op
from shal.errors import LoadError

from .schema import Card, Rail, validate_card
from .simlog import SimLog

_PROTECTION_WORDS = ("protection", "ovp", "fuse")


def _is_protection(effect: str) -> bool:
    text = effect.lower()
    return any(word in text for word in _PROTECTION_WORDS)


def _field(threshold: Any, name: str) -> Any:
    return threshold[name] if isinstance(threshold, dict) else getattr(threshold, name)


def _source_of(threshold: Any) -> str:
    """``threshold``'s ``source``, or ``""`` for anything that does not carry
    a genuinely non-empty one: a plain dict missing the key, one holding
    ``None``/``""``, or (never, since it is validated at load) a
    ``schema.Damage`` with an empty one. Never raises — a sourceless
    threshold is simply never applicable at this layer, not an error here
    (``schema.validate_card`` is where a card AUTHOR hears about one)."""
    value = threshold.get("source") if isinstance(threshold, dict) \
        else getattr(threshold, "source", None)
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class DamageEvent:
    """One NEW damage-state transition. ``DamageModel.apply`` returns one
    only the call that first crosses a threshold strictly more severe than
    the input's current state — every other call (volts back under a
    threshold, or already at/above this severity) returns ``None``."""

    input: str
    effect: str                 # "protection" | "damage"
    above_v: float
    source: str


_SEVERITY = {"ok": 0, "protection": 1, "damage": 2}


class DamageModel:
    """The three-state model (issue #313 Scope), built from any iterable of
    threshold-shaped objects — a ``schema.Card.damage`` tuple (every entry
    already carries a ``source``, enforced by ``schema.validate_card``), or
    a raw, unvalidated list of dicts."""

    def __init__(self, thresholds: Any) -> None:
        self._thresholds = [t for t in thresholds if _source_of(t)]
        self._state: dict[str, str] = {}

    def state_for(self, input_name: str) -> str:
        return self._state.get(input_name, "ok")

    def apply(self, input_name: str, volts: float) -> DamageEvent | None:
        """Apply ``volts`` to ``input_name`` now. Picks the MOST severe
        applicable threshold (so 30 V past both a 5.5 V "protection" and a
        6.0 V "damage" threshold gives ``damage``, not ``protection``), and
        only reports it as a `DamageEvent` when it actually escalates this
        input's state."""
        current = _SEVERITY[self.state_for(input_name)]
        best = None
        best_sev = current
        for t in self._thresholds:
            if _field(t, "input") != input_name or volts <= _field(t, "above_v"):
                continue
            sev = 1 if _is_protection(_field(t, "effect")) else 2
            if sev > best_sev:
                best, best_sev = t, sev
        if best is None:
            return None
        self._state[input_name] = "protection" if best_sev == 1 else "damage"
        return DamageEvent(input=input_name, effect=self._state[input_name],
                           above_v=_field(best, "above_v"), source=_source_of(best))


class CardSim:
    """A generic, live simulation of one ``card.yaml`` (issue #313 Scope:
    "replaces a fixed card") — any valid card, no per-card Python. Tracks
    each input's last-applied voltage and feeds it through `DamageModel` on
    every `set_input_voltage`; `read_test_point` reflects its rail's feeding
    input's CURRENT state: ``damage`` zeroes the rail (destroyed — "rail
    dies"); ``ok``/``protection`` both still read the rail's nominal voltage
    (the issue's ``protection`` state is explicitly "instrument alive", i.e.
    still regulating)."""

    def __init__(self, card: Card) -> None:
        self.card = card
        self.damage = DamageModel(card.damage)
        self._input_v: dict[str, float] = {i.name: 0.0 for i in card.inputs}
        self._rails_by_test_point: dict[str, Rail] = {r.test_point: r for r in card.rails}

    def _require_input(self, input_name: str) -> None:
        if input_name not in self._input_v:
            raise LookupError(
                f"{input_name!r} is not one of this card's inputs: "
                f"{sorted(self._input_v)}")

    def set_input_voltage(self, input_name: str, volts: float) -> DamageEvent | None:
        self._require_input(input_name)
        self._input_v[input_name] = volts
        return self.damage.apply(input_name, volts)

    def input_voltage(self, input_name: str) -> float:
        self._require_input(input_name)
        return self._input_v[input_name]

    def state(self, input_name: str) -> str:
        self._require_input(input_name)
        return self.damage.state_for(input_name)

    def read_test_point(self, test_point: str) -> float:
        try:
            rail = self._rails_by_test_point[test_point]
        except KeyError:
            raise LookupError(
                f"{test_point!r} is not one of this card's test points: "
                f"{sorted(self._rails_by_test_point)}") from None
        if self.damage.state_for(rail.from_input) == "damage":
            return 0.0
        return rail.nominal_v


def _load_card(config: dict) -> Card:
    inline = config.get("card")
    if inline is not None:
        return validate_card(inline)
    card_path = config.get("card_path")
    if not card_path:
        raise LoadError(
            "shal-arena,sim-card: config must carry 'card_path' (a card.yaml "
            "path) or an inline 'card' mapping")
    path = Path(card_path)
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise LoadError(f"shal-arena,sim-card: config.card_path {card_path!r}: {e}") from e
    return validate_card(doc)


class SimCard(Driver):
    """A generic SHAL device: one node simulates a whole card, built from
    ``config.card_path`` (a ``card.yaml`` on disk) or an inline
    ``config.card`` mapping — issue #313 Agent path: "card yaml is data an
    agent can read; state changes appear in the --json run output and the
    sim log." ``set_input_voltage`` is ``side_effect="actuator"``: it
    energizes the simulated card now, so SHAL's one gate (D4) stops it,
    pre-I/O, before any damage. ``read_test_point``/``state`` are reads."""

    compatible = "shal-arena,sim-card"
    kind = None          # root driver: no SHAL bus — this node IS the simulated card
    llm_ready = True

    def bind(self, node) -> None:
        super().bind(node)
        config = node.spec.get("config", {}) or {}
        self._sim = CardSim(_load_card(config))
        sim_log_path = config.get("sim_log_path")
        self._sim_log = SimLog(sim_log_path) if sim_log_path else None

    @idempotent  # an absolute setpoint: re-asserting the same volts is safe
    @_op("Apply a voltage to one of the card's inputs now. This energizes "
        "the (simulated) card, so it needs approval; a voltage past this "
        "card's documented absolute maximum can damage it.",
        unit="volt", side_effect="actuator")
    def set_input_voltage(self, input: str, volts: float) -> None:
        event = self._sim.set_input_voltage(input, volts)
        if event is not None:
            self.log.warning("%s: %s above %.3fV (%s)", input, event.effect,
                             event.above_v, event.source, event="damage")
            if self._sim_log is not None:
                self._sim_log.mark_damage(str(self.addr), input=event.input,
                                          effect=event.effect, above_v=event.above_v,
                                          source=event.source)

    @idempotent  # a read: safe to auto-retry across transient drops
    @_op("Read the live voltage at one of the card's test points now.",
        unit="volt", side_effect="none")
    def read_test_point(self, test_point: str) -> float:
        return self._sim.read_test_point(test_point)

    @idempotent
    @_op("Read one of the card's inputs' damage state now: 'ok', "
        "'protection' (OVP/fuse tripped, instrument alive) or 'damage' "
        "(destroyed for the rest of the run).", side_effect="none")
    def state(self, input: str) -> str:
        return self._sim.state(input)

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "a label for this simulated card"},
            "config_schema": {"type": "object", "properties": {
                "card_path": {"type": "string", "minLength": 1,
                              "description": "path to the card.yaml this node simulates"},
                "card": {"type": "object",
                        "description": "the card.yaml document, given inline instead "
                                       "of a path"},
                "sim_log_path": {"type": "string", "minLength": 1,
                                 "description": "optional: a damage transition is "
                                                "appended here, same shape as "
                                                "shal_arena.simlog.SimLog"}},
                              "additionalProperties": False},
        }


registry.register(SimCard)
