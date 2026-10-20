"""Start the demo from any stage (issue #508; CTO ruling on #508).

    shal-arena demo --from racked --json

Five stages, in order: ``empty, picked, racked, wired, ran``. Each one is a
preset file, ``shal_arena/presets/<stage>.yaml`` (package data): the setup
YAML the rack page writes (`replay.rack.build_setup_yaml`) plus one top-level
``stage:`` key. ``empty`` has only ``stage``; ``ran`` also has ``run: {seed:
<int>}``. Each stage holds everything in the stage before it.

No new schema: `load_preset` takes ``stage``/``run`` off and hands the rest to
`shal.load`, the existing loader, unchanged. The bench binds the packaged
reference drivers (`ui.play.REFERENCE_DRIVERS`, what Play uses) and their sim
models, so a preset needs no driver of the player's own.

``--from X`` runs one step per stage, from X to ``ran``; the first step is X.
side_effect: none -- it reads the preset files and the sims, and writes
nothing (no state dir, no run). The one gated op the ``ran`` step asks for is
refused by the gate (`shal.DenyAll`): nothing is sent."""
from __future__ import annotations

import contextlib
import importlib
import json
import sys
import time
import traceback
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import resources as importlib_resources
from typing import Any

import yaml

from .errors import ArenaError, TaskFormatError

STAGES: tuple[str, ...] = ("empty", "picked", "racked", "wired", "ran")

#: the start card the demo page marks first (issue #520)
DEFAULT_STAGE = "racked"

#: the keys a preset adds on top of the rack page's setup YAML
_META_KEYS = ("stage", "run")

#: the one gated op the ``ran`` step asks for: the PSU's own setpoint, at the
#: same 3.3 V the main story's first step sets (`demo._run_virtual_bench_pass`)
_GATED_OP, _GATED_ARGS = "set_voltage", {"volts": 3.3}

_LINES = {
    "empty": "Starting from an empty bench: no instrument is picked yet.",
    "picked": "Loading the picked instruments with their drivers.",
    "racked": "Loading the full rack: every instrument in its slot.",
    "wired": "Reading every instrument on the wired bench.",
    "ran": "Running the bench from its fixed seed: every read, then one gated "
           "op the gate must stop.",
}


class UnknownStage(ArenaError):
    """``--from`` named a stage that is not one of `STAGES`. Exit 2: a usage
    mistake, not a crash."""

    exit_code = 2


@dataclass(frozen=True)
class Preset:
    stage: str
    topology: dict[str, Any] | None  # the rack page's setup YAML; None for `empty`
    seed: int | None  # `run.seed`; only `ran` has one


def _unknown_stage(stage: str) -> UnknownStage:
    return UnknownStage(
        f"--from: unknown stage {stage!r}; the 5 valid stages are: {', '.join(STAGES)}",
        fix="pass one of them, e.g. `shal-arena demo --from racked --json`")


def load_preset(stage: str) -> Preset:
    """The packaged preset for ``stage``. `UnknownStage` names the 5 valid
    stages; a preset whose own ``stage:`` disagrees with its file name is a
    `TaskFormatError`."""
    if stage not in STAGES:
        raise _unknown_stage(stage)
    ref = importlib_resources.files("shal_arena") / "presets" / f"{stage}.yaml"
    doc = yaml.safe_load(ref.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or doc.get("stage") != stage:
        raise TaskFormatError(f"presets/{stage}.yaml: `stage:` must be {stage!r}",
                              fix=f"set `stage: {stage}` at the top of presets/{stage}.yaml")
    topology = {k: v for k, v in doc.items() if k not in _META_KEYS} or None
    return Preset(stage=stage, topology=topology, seed=(doc.get("run") or {}).get("seed"))


def _case_names(topology: dict[str, Any]) -> list[str]:
    from .cases import CASES

    by_compatible = {c.compatible: name for name, c in CASES.items()}
    return [by_compatible[child["driver"]]
            for bus in topology["root"].values()
            for child in (bus.get("children") or {}).values()]


def _reference_driver_class(case_name: str) -> type:
    from .ui.play import reference_driver_for

    module = importlib.import_module(
        f"shal_arena.reference_drivers.{reference_driver_for(case_name).stem}")
    from .cases import CASES

    compatible = CASES[case_name].compatible
    return next(v for v in vars(module).values()
                if isinstance(v, type) and getattr(v, "compatible", None) == compatible)


@contextlib.contextmanager
def bench(preset: Preset) -> Iterator[Any]:
    """`shal.load` of the preset's topology, with each case's reference
    driver and sim model bound, and `shal.DenyAll` as its approver (a gated op
    is refused, never sent, never asks a person). The registry slots are put
    back afterwards (`demo._registry_slots_restored`)."""
    import shal
    from shal import registry

    from .cases import resolve_case
    from .demo import _registry_slots_restored

    if preset.topology is None:
        raise ArenaError(f"stage {preset.stage!r} has no bench to load",
                         fix="start from a later stage, e.g. `shal-arena demo --from "
                             "picked --json`")
    names = _case_names(preset.topology)
    compatibles = [resolve_case(n).compatible for n in names]  # also imports each sim model
    with _registry_slots_restored(*compatibles):
        for name in names:
            registry.register(_reference_driver_class(name), override=True)
        hal = shal.load(preset.topology, approver=shal.DenyAll())
        try:
            yield hal
        finally:
            hal.close()


def _reads(hal: Any) -> dict[str, Any]:
    return {t["name"]: hal.call_tool(t["name"], {}) for t in hal.tool_catalog()
            if t["side_effect"] == "none"}


def _step(stage: str) -> tuple[dict[str, Any], bool]:
    preset = load_preset(stage)
    if preset.topology is None:
        return ({"instruments": [], "next": "pick instruments: "
                 "`shal-arena demo --from picked --json`"}, True)
    with bench(preset) as hal:
        catalog = hal.tool_catalog()
        instruments = sorted({t["device"] for t in catalog})
        result: dict[str, Any] = {"instruments": instruments,
                                  "tools": [t["name"] for t in catalog]}
        ok = len(instruments) == len(_case_names(preset.topology))
        if stage in ("wired", "ran"):
            reads = _reads(hal)
            result["reads"] = reads
            ok = ok and bool(reads) and all(r["ok"] for r in reads.values())
        if stage == "ran":
            tool = next(t["name"] for t in catalog if t["op"] == _GATED_OP)
            called = hal.call_tool(tool, dict(_GATED_ARGS))
            result["seed"] = preset.seed
            result["gate"] = {"tool": tool, "rejected": called.get("rejected"),
                              "sent": "rejected" not in called}
            ok = ok and isinstance(preset.seed, int) and called.get("rejected") == "approval"
    return result, ok


def run_from(stage: str, *, pause: float, json_mode: bool) -> int:
    """One step per stage from ``stage`` to ``ran``, each from its own preset
    -- the first step is ``stage``. Plain lines, or one JSON document on
    stdout. Raises `UnknownStage` before any step runs."""
    from .demo import FIRST_LINE

    if stage not in STAGES:
        raise _unknown_stage(stage)
    if not json_mode:
        print(FIRST_LINE)
    steps: list[dict[str, Any]] = []
    overall_ok = True
    for step_id in STAGES[STAGES.index(stage):]:
        started = time.monotonic()
        time.sleep(pause)
        line = _LINES[step_id]
        if not json_mode:
            print(line)
        try:
            result, ok = _step(step_id)
        except Exception as e:  # noqa: BLE001 - one stage's crash must not hide the rest
            traceback.print_exc()
            result = {"crashed": f"{type(e).__name__}: {e}",
                      "fix": getattr(e, "fix", None) or (
                          f"see the traceback on stderr; check presets/{step_id}.yaml")}
            ok = False
        overall_ok = overall_ok and ok
        if not json_mode:
            print(f"  -> {'ok' if ok else 'failed'}")
            sys.stdout.flush()
        steps.append({"step": step_id, "line": line, "sentence": line,
                      "step_ms": round((time.monotonic() - started) * 1000, 1),
                      "ok": ok, "result": result})
    if json_mode:
        print(json.dumps({"ok": overall_ok, "note": FIRST_LINE, "from": stage,
                          "side_effect": "none", "steps": steps}, indent=2, default=str))
    return 0 if overall_ok else 1
