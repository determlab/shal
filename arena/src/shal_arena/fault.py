"""Fault injection at run time, from the seed (issue #312 Scope).

The fault id AND its per-run numeric parameters are drawn once, from a single
``random.Random(seed)`` sequence — so the same seed always realizes the same
fault (and, for ``noise``, the same realized ripple), and a different seed
can realize a different one. ``realized_fault`` never writes anything to
disk; the caller decides what (if anything) gets persisted, and only after
the run is closed (``runner.answer``), same discipline as ``store.py``.

Three fault *types* ship today, matching ``card.yaml``'s ``faults:`` ids
(the CTO's task/card format ruling, issue #310): ``low_voltage`` and
``noise`` shift/perturb the probing instrument's reading (wired onto the sim
model via ``harness_for_run``'s generated ``config:``); ``open`` is an open
circuit on the card (issue #477, CTO decision: an ``open`` card is measured,
not unreachable): the instrument still answers, and the rail reads about
0 V. A broken link to the bench (shal core's ``fault: unplugged`` /
``SHAL_SIM_UNPLUG``) is a different thing — an ``error``, never a card fault.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schema import Card, Rail, TempPoint

# the realized ripple for a 'noise' run is the card's own ripple_vpp, rescaled
# by this seed-derived factor — so two different seeds that both happen to
# pick 'noise' still realize two different ripples (Done-when: "a different
# seed differs"), while the SAME seed always rescales it the same way.
_NOISE_SCALE_RANGE = (0.7, 1.3)

# issue #477: an ``open`` rail reads a small residual, at most this fraction
# of the rail's nominal (well below the 5% that names ``open``) -- drawn from
# its own seed-only RNG, so it never shifts the `realized_fault` sequence
# above and the same seed reads the same value on every call and every OS.
_OPEN_RESIDUAL_FRACTION = 0.005


def open_residual_v(rail: Rail, seed: int) -> float:
    """The reading an ``open`` ``rail`` gives for ``seed``: about 0 V, the
    same for every call of the same seed (no ``nonce``), rounded to the
    DMM's own 6 decimals so it formats identically everywhere."""
    rng = random.Random(f"open:{seed}")
    return round(rng.uniform(0.0, _OPEN_RESIDUAL_FRACTION) * rail.nominal_v, 6)


@dataclass(frozen=True)
class RealizedFault:
    fault_id: str
    extra: dict[str, Any] = field(default_factory=dict)


def realized_fault(card: Card, seed: int) -> RealizedFault:
    """The fault this run has, realized deterministically from ``seed``
    alone: same seed -> same `RealizedFault` (fault id AND noise), a
    different seed can give a different one of either."""
    rng = random.Random(seed)
    fault = rng.choice(card.faults)
    noise_scale = rng.uniform(*_NOISE_SCALE_RANGE)
    extra = dict(fault.extra)
    if fault.id == "noise" and "ripple_vpp" in extra:
        extra["ripple_vpp"] = extra["ripple_vpp"] * noise_scale
    return RealizedFault(fault_id=fault.id, extra=extra)


def rail_for_fault(card: Card, realized: RealizedFault) -> Rail | None:
    """The card rail ``realized`` targets, or `None` for a fault with no
    ``rail`` (``ok``, or a future fault kind that isn't tied to one rail)."""
    rail_name = realized.extra.get("rail")
    if rail_name is None:
        return None
    return next((r for r in card.rails if r.name == rail_name), None)


def temp_point_for_fault(card: Card, realized: RealizedFault) -> TempPoint | None:
    """The card temperature point ``realized`` targets (e.g. ``overheat``),
    or `None` for a fault with no ``temp_point`` — the temperature analogue
    of `rail_for_fault`."""
    temp_name = realized.extra.get("temp_point")
    if temp_name is None:
        return None
    return next((t for t in card.temp_points if t.name == temp_name), None)


def harness_for_run(case: Any, *, rail: Rail, realized: RealizedFault, seed: int,
                    nonce: int = 0) -> dict:
    """The case's harness topology, generated in memory for THIS run only —
    never written to any file (Scope: "never written to a file the player or
    agent can read") — with ``realized`` wired onto the one child node under
    test:

    - ``open`` (issue #477): the node gets a ``config:`` with ``open_v``, the
      seed's own `open_residual_v` -- the sim model reads it instead of the
      rail's nominal, so the instrument answers about 0 V. No hop raises.
    - ``low_voltage`` / ``noise``: the node gets a ``config:`` carrying the
      rail's nominal voltage plus the realized shift/ripple, read by the
      case's sim model at bind time (``bind_sim``, the same hook shal core's
      own ``SimDmmModel`` uses).
    - ``ok``: the caller should not need this at all — see
      ``runner._topology_for_instrument``, which returns the case's static
      harness unchanged when there is nothing to inject.

    ``nonce`` (issue #431 bug fix): this harness is rebuilt fresh on every
    CLI call (a separate process each time), with no state of its own
    carried between them -- `config["seed"]` used to be the run's own
    ``seed`` alone, so the sim model's own ripple RNG started from the SAME
    state on every single call, and ``noise`` read back the exact same
    "random" value every time. The caller passes something that varies
    call to call (``state.turns``, already incremented before this run) so
    two measurements of the same run's noise realize two different ripple
    samples, while the SAME (seed, nonce) pair still reproduces the same
    one -- this file's own rule ("same seed always realizes the same ...
    ripple") still holds for one fixed call.
    """
    doc = yaml.safe_load(Path(case.harness_topology).read_text(encoding="utf-8"))
    bench = next(iter(doc["root"].values()))
    _child_key, child = next(iter(bench["children"].items()))

    if realized.fault_id == "open":
        child["config"] = {"nominal_v": rail.nominal_v,
                           "open_v": open_residual_v(rail, seed)}
        return doc

    config: dict[str, Any] = {"nominal_v": rail.nominal_v}
    shift_v = realized.extra.get("shift_v")
    if shift_v is not None:
        config["shift_v"] = shift_v
    ripple_vpp = realized.extra.get("ripple_vpp")
    if ripple_vpp is not None:
        config["ripple_vpp"] = ripple_vpp
        config["seed"] = seed * 1_000_003 + nonce
    child["config"] = config
    return doc
