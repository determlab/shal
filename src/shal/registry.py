"""Driver registry — keyed by `compatible` (DESIGN V2 decision 3).

Discovery via entry points (group ``shal.drivers``): drivers run because they were
installed on purpose. The framework never imports a module named by a config string.
Bundled drivers register explicitly below.

Collision policy (closes the v1 silent last-write-wins overwrite): registering a
*different* class under a `compatible` that another class already claims is kept as
a candidate, not silently dropped. The clash surfaces at ``resolve`` — loudly,
naming each providing distribution — unless the node disambiguates with ``from:``
or a caller registered with ``override=True``. Re-registering the *same* class is
an idempotent no-op (the bundled drivers register via both their ``@register``
decorator and the entry-point load).
"""
from __future__ import annotations

from importlib.metadata import entry_points, packages_distributions
from typing import TYPE_CHECKING

from .errors import LoadError

if TYPE_CHECKING:
    from .driver import Driver

ENTRY_POINT_GROUP = "shal.drivers"

# compatible -> ordered list of distinct candidate classes
_entries: dict[str, list[type[Driver]]] = {}
_eps_loaded = False


def register(cls: type[Driver], *, override: bool = False) -> type[Driver]:
    """Register a driver class under its `compatible`.

    Same class re-registered -> no-op. A different class is appended as a
    candidate (the clash is reported at resolve). `override=True` is the explicit
    "I mean to shadow whatever else claims this id" escape hatch — it drops the
    existing candidates first.
    """
    compatible = getattr(cls, "compatible", None)
    if not compatible:
        raise LoadError(f"driver {cls.__name__} has no `compatible`")
    candidates = _entries.setdefault(compatible, [])
    if override:
        candidates.clear()
    if cls not in candidates:
        candidates.append(cls)
    return cls


def _load_entry_points() -> None:
    global _eps_loaded
    if _eps_loaded:
        return
    _eps_loaded = True
    for ep in entry_points(group=ENTRY_POINT_GROUP):
        register(ep.load())


def _distribution_of(cls: type) -> str:
    """Best-effort: the installed distribution that ships `cls` (for clash messages)."""
    top = (getattr(cls, "__module__", "") or "").split(".")[0]
    try:
        dists = packages_distributions().get(top)
    except Exception:  # pragma: no cover - importlib edge cases
        dists = None
    if dists:
        return dists[0]
    return top or "<unknown>"


def resolve(compatible: str, *, prefer_dist: str | None = None) -> type[Driver]:
    """Unknown compatible fails the load — loudly, with the fix in the message.
    Ambiguous compatible (two distributions claim it) also fails loudly unless
    `prefer_dist` (the node's `from:` key) selects exactly one."""
    _load_entry_points()
    _ensure_bundled()
    candidates = _entries.get(compatible)
    if not candidates:
        raise LoadError(
            f"no driver installed for compatible '{compatible}' "
            f"(install a package exposing it via the '{ENTRY_POINT_GROUP}' entry point)"
        )
    if prefer_dist is not None:
        matched = [c for c in candidates if _distribution_of(c) == prefer_dist]
        if not matched:
            have = ", ".join(sorted({_distribution_of(c) for c in candidates}))
            raise LoadError(
                f"compatible '{compatible}' is not provided by '{prefer_dist}' "
                f"(from:); available from: {have}")
        candidates = matched
    if len(candidates) == 1:
        return candidates[0]
    listing = "; ".join(f"{c.__module__}.{c.__qualname__} ({_distribution_of(c)})"
                        for c in candidates)
    raise LoadError(
        f"compatible '{compatible}' is claimed by {len(candidates)} drivers: {listing}. "
        f"Disambiguate with a node `from: <distribution>`, uninstall one, or register "
        f"the intended class with override=True.")


_bundled_loaded = False


def _ensure_bundled() -> None:
    """Bundled drivers ship with the package == installed on purpose."""
    global _bundled_loaded
    if _bundled_loaded:
        return
    _bundled_loaded = True
    from .buses import (  # noqa: F401  (each module registers its compatible)
        http_bus,
        i2c_cli,
        local,
        mux,
        sim,
        spi_cli,
        ssh,
        tcp,
    )
    from .drivers import tmp102  # noqa: F401  (registers ti,tmp102)
