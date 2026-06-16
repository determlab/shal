"""Cold-start guard shim — copied into the throwaway venv as ``sitecustomize.py``.

Python imports ``sitecustomize`` automatically at interpreter startup (if it is on the
path), so this runs before the evaluator agent's code does, inside the venv only.

Its job: turn the cold-start honor rule ("do not use the bundled sonos driver, do not
use a built-in simulator") into an ENFORCED wall. SHAL registers the bundled
``sonos,speaker`` driver and the ``*,sim*`` buses UNCONDITIONALLY on the first
``shal.load`` (registry._ensure_bundled / entry-points), regardless of which extras are
installed — so banning the import / the ``pyshal[sonos]`` extra in prose is not enough;
a topology of ``driver: sonos,speaker`` + ``address: sim`` returns fabricated, real-
looking read data with zero dependencies.

This shim eagerly forces the registry to load all its entries, then DROPS the forbidden
compatibles and freezes the load flags so nothing re-adds them. After this, any
``shal.load`` of ``sonos,speaker`` (or any ``shal,sim*`` bus) raises the library's own
``LoadError('no driver installed ...')`` — a genuine wall the agent cannot tiptoe past.

It is intentionally fail-open: if the installed pyshal is shaped differently than
expected, the shim does nothing rather than crashing the interpreter (the agent's
report + verify_run.py remain the backstop). It only ever REMOVES bundled entries; it
never touches a driver the agent authors under its own compatible.
"""
from __future__ import annotations


def _install_cold_start_guard() -> None:
    try:
        from shal import registry
    except Exception:
        return  # pyshal not importable yet / shaped differently — nothing to guard.

    # Force the registry to populate its entry-point + bundled tables now, so the
    # forbidden compatibles are present and can be removed (and won't be re-added).
    for fn_name in ("_load_entry_points", "_ensure_bundled"):
        fn = getattr(registry, fn_name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                return  # registry internals differ — fail open.

    entries = getattr(registry, "_entries", None)
    if not isinstance(entries, dict):
        return  # registry internals differ — fail open.

    # Drop the bundled sonos driver and every built-in simulator compatible. Matching:
    #   - exactly 'sonos,speaker' (the bundled hero driver), and
    #   - any compatible containing 'sim' (e.g. 'shal,sim-i2c', 'shal,sim-scpi',
    #     'shal,sim-msg') — the simulator buses that return fabricated read data.
    forbidden = [
        comp for comp in list(entries)
        if comp == "sonos,speaker" or "sim" in comp.lower()
    ]
    for comp in forbidden:
        entries.pop(comp, None)

    # Freeze: even if something calls the loaders again, they early-return now, and
    # re-registration of the dropped compatibles is prevented.
    if hasattr(registry, "_eps_loaded"):
        registry._eps_loaded = True
    if hasattr(registry, "_bundled_loaded"):
        registry._bundled_loaded = True


_install_cold_start_guard()
