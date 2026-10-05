"""A card simulated from yaml, with a three-state damage model.

States (``CardSim.state``):

- ``ok``         nothing over a limit.
- ``protection`` an OVP or fuse tripped; the instrument is alive. A tripped
                 rail reads 0 V while the input stays over the limit and recovers
                 when it comes back in range.
- ``damage``     the card is destroyed for the rest of the run: every rail reads
                 0 V and the card's supply current jumps to ``short_a``.

Every limit is a datasheet absolute maximum and names its ``source``. A limit
without a ``source`` is loaded but never counted as damage (it is listed in
``CardSim.ignored_limits``). ``replacement_usd`` is not read here: it lives in
the arena catalogue (the task's instrument entries / the instrument yaml given
to `load_instruments`), never in a SHAL manifest.

Damage is appended to the sim log as ``kind: "damage"`` / ``"protection"`` /
``"refused"`` lines (same JSON-lines shape as `simlog.SimLog`). The optional
``gate`` is the approval step: a callable taking the proposed action dict and
returning True to allow. When the action would breach a documented limit and
the gate says no, nothing is applied (the card stays as it was).
"""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..errors import ArenaError, TaskFormatError

OK = "ok"
PROTECTION = "protection"
DAMAGE = "damage"

_EFFECTS = {"protection": PROTECTION, "damage": DAMAGE, "destroyed": DAMAGE}

Gate = Callable[[dict[str, Any]], bool]


@dataclass(frozen=True)
class Limit:
    input: str
    above_v: float
    effect: str            # PROTECTION | DAMAGE
    source: str | None     # datasheet reference; None -> never counted

    @property
    def documented(self) -> bool:
        return bool(self.source and str(self.source).strip())


@dataclass(frozen=True)
class RailSpec:
    name: str
    from_input: str
    nominal_v: float
    test_point: str


@dataclass(frozen=True)
class InstrumentSpec:
    id: str
    replacement_usd: float
    fuse_a: float | None = None
    fuse_source: str | None = None


@dataclass
class Result:
    ok: bool
    state: str
    sent: bool = True
    rejected: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        # apply_input is this Result's only producer, and it always changes the
        # card's own data and appends to the sim log -- side_effect: write,
        # whether or not the gate ended up sending it (CTO review, issue #313).
        d: dict[str, Any] = {"ok": self.ok, "state": self.state, "sent": self.sent,
                             "side_effect": "write"}
        if self.rejected:
            d["rejected"] = self.rejected
        d.update(self.detail)
        return d


def _num(v: Any, where: str) -> float:
    # card yaml is community data read by whatever loads this card (CTO
    # review, issue #313): a bad value must name the fix, not crash with a
    # bare ValueError.
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise TaskFormatError(f"{where}: must be a number, got {v!r}",
                              fix=f"set {where} to a number")
    return float(v)


class CardSim:
    def __init__(self, doc: dict[str, Any], *, log_path: str | Path | None = None) -> None:
        self.id = str(doc["id"])
        self.inputs = {n: _num(e["nominal_v"], f"inputs.{n}.nominal_v")
                       for n, e in (doc.get("inputs") or {}).items()}
        self.rails = {
            n: RailSpec(n, e["from"], _num(e["nominal_v"], f"rails.{n}.nominal_v"),
                        e["test_point"])
            for n, e in (doc.get("rails") or {}).items()}
        self.limits: list[Limit] = []
        for i, e in enumerate(doc.get("limits", doc.get("damage")) or []):
            effect = _EFFECTS.get(e.get("effect"))
            if effect is None:
                raise TaskFormatError(
                    f"limits[{i}].effect: must be one of {sorted(_EFFECTS)}, "
                    f"got {e.get('effect')!r}",
                    fix=f"set limits[{i}].effect to one of {sorted(_EFFECTS)}")
            self.limits.append(Limit(e["input"], _num(e["above_v"], f"limits[{i}].above_v"),
                                     effect, e.get("source")))
        self.faults = [f["id"] for f in doc.get("faults") or []]
        self.short_a = _num(doc.get("short_a", 10.0), "short_a")
        self.supply_a = _num(doc.get("supply_a", 0.05), "supply_a")
        self.log_path = Path(log_path) if log_path else None
        self.applied: dict[str, float] = dict(self.inputs)
        self.destroyed = False

    # -- introspection -------------------------------------------------- #
    @property
    def ignored_limits(self) -> list[Limit]:
        return [lim for lim in self.limits if not lim.documented]

    def _breaches(self, volts: dict[str, float]) -> list[Limit]:
        return [lim for lim in self.limits
                if lim.documented and volts.get(lim.input, 0.0) > lim.above_v]

    @property
    def state(self) -> str:
        if self.destroyed:
            return DAMAGE
        return PROTECTION if self._breaches(self.applied) else OK

    # -- measurements --------------------------------------------------- #
    def rail_voltage(self, rail: str) -> float:
        spec = self.rails[rail]
        if self.state != OK:
            return 0.0
        return spec.nominal_v

    def test_point_voltage(self, test_point: str) -> float:
        # a plain next() with no default raises StopIteration on a miss, which
        # a player reading this card's own wiring would never see named (CTO
        # review, issue #313).
        rail = next((r for r in self.rails.values() if r.test_point == test_point), None)
        if rail is None:
            known = sorted(r.test_point for r in self.rails.values())
            raise ArenaError(f"no such test point {test_point!r}",
                             fix=f"probe one of this card's test points: {known}")
        return self.rail_voltage(rail.name)

    def supply_current(self) -> float:
        return self.short_a if self.destroyed else self.supply_a

    # -- actions -------------------------------------------------------- #
    def apply_input(self, name: str, volts: float, *, gate: Gate | None = None,
                    address: str = "card") -> Result:
        if name not in self.inputs:
            # a bare KeyError is a crash, not a named failure, for data read
            # from the community card yaml (CTO review, issue #313).
            raise ArenaError(f"unknown input {name!r}",
                             fix=f"drive one of this card's inputs: {sorted(self.inputs)}")
        if self.destroyed:  # nothing more can happen to a dead card
            self.applied[name] = volts
            return Result(True, DAMAGE, detail={"input": name, "volts": volts})
        proposed = dict(self.applied, **{name: volts})
        breaches = self._breaches(proposed)
        if breaches and gate is not None:
            action = {"op": "apply_input", "input": name, "volts": volts,
                      "would_cause": max((b.effect for b in breaches),
                                         key=lambda e: e == DAMAGE)}
            if not gate(action):
                self._log(address, "refused", input=name, volts=volts)
                return Result(False, self.state, sent=False, rejected="approval",
                              detail={"input": name, "volts": volts})
        self.applied = proposed
        worst = next((b for b in breaches if b.effect == DAMAGE), None)
        if worst is not None:
            self.destroyed = True
            self._log(address, "damage", input=name, volts=volts,
                      limit_v=worst.above_v, source=worst.source)
        elif breaches:
            self._log(address, "protection", input=name, volts=volts,
                      limit_v=breaches[0].above_v, source=breaches[0].source)
        return Result(True, self.state, detail={"input": name, "volts": volts})

    def _log(self, address: str, kind: str, **extra: Any) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "address": address, "kind": kind, **extra}
        with self.log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


class Dmm:
    """A DMM current input with a fuse: over-current burns it (instrument state
    ``protection``) and every later current reading is 0 A."""

    def __init__(self, spec: InstrumentSpec, *, log_path: str | Path | None = None,
                 address: str = "dmm0") -> None:
        self.spec = spec
        self.address = address
        self.fuse_blown = False
        self._log_path = Path(log_path) if log_path else None

    @property
    def state(self) -> str:
        return PROTECTION if self.fuse_blown else OK

    def measure_current(self, amps: float) -> float:
        if self.fuse_blown:
            return 0.0
        if self.spec.fuse_a is not None and self.spec.fuse_source and amps > self.spec.fuse_a:
            self.fuse_blown = True
            if self._log_path is not None:
                self._log_path.parent.mkdir(parents=True, exist_ok=True)
                entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         "address": self.address, "kind": "protection", "fuse_a": self.spec.fuse_a,
                         "amps": amps, "source": self.spec.fuse_source}
                with self._log_path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry) + "\n")
            return 0.0
        return amps


def load_card_sim(path: str | Path, *, log_path: str | Path | None = None) -> CardSim:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CardSim(doc, log_path=log_path)


def load_instruments(path: str | Path) -> dict[str, InstrumentSpec]:
    """Instrument catalogue yaml: ``instruments: {id: {replacement_usd, fuse_a,
    fuse_source}}``. This is where ``replacement_usd`` lives."""
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    out = {}
    for iid, e in (doc.get("instruments") or {}).items():
        out[iid] = InstrumentSpec(iid, _num(e["replacement_usd"], f"{iid}.replacement_usd"),
                                  e.get("fuse_a"), e.get("fuse_source"))
    return out
