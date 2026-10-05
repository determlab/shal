"""The packaged ADK cases (CTO ruling, issue #310): each ``case:`` named in a
``task.yaml`` is one of these. A case ships two things, same split as
`shal docs --example <name>` for the main SDK:

- ``docs/`` — a datasheet-style description of a fictional instrument's
  command set. This is what the player reads to write ``driver.py``; nothing
  in it, or in the harness topology below, is the hidden fault (DoD 4).
- ``harness/`` — the reference sim (``sim.py``, a ``shal,sim-scpi``
  ``@scpi_sim_model``) and the topology (``topology.yaml``) that binds a node
  of ``driver: <compatible>`` to it. The player never sees this folder; the
  runner imports the player's ``driver.py`` (which registers the same
  ``compatible``, exactly like an ADK reference driver) and then runs
  `shal.conformance.check_driver(compatible, topology=harness/topology.yaml)`
  against it — the "ADK-style driver check" in the issue's Scope.

Building new instrument sims is explicitly out of scope for this ticket
(issue #310 "Out of scope: ... sims"); both cases below reuse `shal`'s own
``shal,sim-scpi`` bus (already shipped) the same way every ADK reference
driver does (e.g. ``rigol_dp832``) — no new simulator code, just two small
fictional command sets a player can implement from the datasheet alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import TaskFormatError

_CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass(frozen=True)
class CaseSpec:
    name: str
    compatible: str          # the `driver:`/`compatible` string the player's driver.py registers
    docs_dir: Path            # given to the player
    harness_topology: Path    # NOT given to the player; passed to conformance.check_driver


def _case(name: str, compatible: str) -> CaseSpec:
    root = _CASES_DIR / name
    return CaseSpec(name=name, compatible=compatible,
                     docs_dir=root / "docs", harness_topology=root / "harness" / "topology.yaml")


# The packaged catalogue (issue #310's two worked-example instruments). Later
# arena tickets add cases here; this ticket ships the mechanism plus these two.
CASES: dict[str, CaseSpec] = {
    "scpi-psu": _case("scpi-psu", "arena,bench-psu1"),
    "dmm": _case("dmm", "arena,bench-dmm1"),
}


def resolve_case(name: str) -> CaseSpec:
    """The `CaseSpec` for a task's ``case:`` value, or `TaskFormatError` naming
    the fix (CTO ruling: "`case` must exist in the packaged ADK cases")."""
    try:
        return CASES[name]
    except KeyError:
        known = ", ".join(sorted(CASES)) or "(none packaged)"
        raise TaskFormatError(
            f"instruments: unknown case {name!r}",
            fix=f"use one of the packaged cases: {known}") from None
