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


CATALOG_SCHEMA_VERSION = "1.0"


def _summary(cls: type) -> str:
    doc = (cls.__doc__ or "").strip()
    return doc.splitlines()[0].strip() if doc else ""


def _capability_of(cls: type) -> str | None:
    """The capability Protocol a driver implements (derived from the MRO)."""
    for c in cls.__mro__:
        if c is not cls and getattr(c, "_is_protocol", False):
            return c.__name__
    return None


def _provides_kinds(cls: type) -> list[str]:
    from .transport import (
        ByteTransport,
        CommandTransport,
        MessageTransport,
        Stream,
    )
    return [k.__name__ for k in (ByteTransport, CommandTransport,
                                 MessageTransport, Stream) if issubclass(cls, k)]


def _op_entries(cls: type) -> list[dict]:
    from . import limits as _limits
    ops = []
    for name, fn in cls.capability_ops().items():
        meta = getattr(fn, "__shal_op__", None) or {}
        idem = bool(getattr(fn, "__shal_idempotent__", False))
        side = meta.get("side_effect") or ("none" if idem else "write")
        ops.append({
            "name": name,
            "description": meta.get("description"),
            "unit": meta.get("unit"),
            # class-level merged schema: the device envelope (a bound node may
            # narrow it further via op_limits()/config.limits — issue #10)
            "input_schema": _limits.merged_params_schema(fn),
            "annotations": {"readOnlyHint": side == "none",
                            "idempotentHint": idem,
                            "destructiveHint": side == "actuator"},
        })
    return ops


def _catalog_entry(compatible: str, cls: type, *, detail: bool) -> dict:
    from .transport import Transport
    is_bus = issubclass(cls, Transport)
    kind = getattr(cls, "kind", None)
    entry: dict = {
        "compatible": compatible,
        "role": "bus" if is_bus else "driver",
        "summary": _summary(cls),
        "requires_parent_kind": kind.__name__ if kind is not None else None,
    }
    if is_bus:
        entry["provides_kinds"] = _provides_kinds(cls)
    else:
        entry["capability"] = _capability_of(cls)
    if not detail:
        return entry
    am = cls.authoring_meta() if hasattr(cls, "authoring_meta") else {}
    entry["address_schema"] = am.get("address_schema")
    entry["config_schema"] = am.get("config_schema")
    if is_bus:
        entry["child_address_schema"] = am.get("child_address_schema")
    else:
        entry["ops"] = _op_entries(cls)
    return entry


def catalog(compatible: str | None = None) -> dict:
    """Authoring view of every registered driver/bus, for constructing topologies
    (issue #1). ``catalog()`` returns compact summaries (progressive disclosure);
    ``catalog(compatible)`` returns one entry in full (address/config schema, ops).
    Everything but the ``authoring_meta()`` schemas is derived from the class."""
    _load_entry_points()
    _ensure_bundled()
    if compatible is not None:
        candidates = _entries.get(compatible)
        if not candidates:
            raise LoadError(f"no driver/bus registered for compatible '{compatible}'")
        return _catalog_entry(compatible, candidates[0], detail=True)
    buses: list[dict] = []
    drivers: list[dict] = []
    for comp, candidates in sorted(_entries.items()):
        for cls in candidates:
            entry = _catalog_entry(comp, cls, detail=False)
            (buses if entry["role"] == "bus" else drivers).append(entry)
    return {"schema_version": CATALOG_SCHEMA_VERSION, "buses": buses, "drivers": drivers}


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
        scpi_raw,
        sim,
        spi_cli,
        ssh,
        tcp,
    )
    from .drivers import (  # noqa: F401  (register their compatibles)
        ads1115,
        ina219,
        keysight_34461a,
        mcp9808,
        mcp23017,
        rigol_dp832,
        tmp102,
    )
