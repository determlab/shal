"""Emit registry-convention manifests for a generated driver (issue #10).

Adopts the artifact conventions from the SHAL Registry Architecture doc
(identifier scheme, definition fields, verification levels, layout) WITHOUT the
registry infrastructure (no search/install/CI). The device definition is DERIVED
from `shal.catalog(compatible)` — the same declared metadata the framework already
owns — so the manifest never drifts from the driver. Per-case `_seed` carries only
the bits the catalog can't know: the `device://` id, category, display names,
documentation references, and credential REQUIREMENTS (never secrets).

    python examples/driver-creator/emit_manifest.py            # all cases
    python examples/driver-creator/emit_manifest.py sht31
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

import shal

ROOT = Path(__file__).resolve().parent

# device:// vendor/category/model + the non-derivable registry fields, per case.
_SEED = {
    "sht31": {
        "compatible": "sensirion,sht31",
        "id": "device://sensirion/environmental_sensor/sht31",
        "vendor": "Sensirion", "model": "SHT31-DIS",
        "category": "environmental_sensor", "protocols": ["i2c"],
        "authentication": {"type": "none", "required_secrets": []},
        "documentation": ["examples/driver-creator/sht31/docs/sht31-datasheet.md"],
    },
    "scpi-psu": {
        "compatible": "vexar,vx3210",
        "id": "device://vexar/power_supply/vx3210",
        "vendor": "Vexar", "model": "VX3210",
        "category": "power_supply", "protocols": ["scpi", "tcp"],
        "authentication": {"type": "none", "required_secrets": []},
        "documentation": ["examples/driver-creator/scpi-psu/docs/vx3210-manual.md"],
    },
    "http-service": {
        "compatible": "lumen,chamber-api",
        "id": "device://lumen/environmental_chamber/chamberlink",
        "vendor": "Lumen", "model": "ChamberLink",
        "category": "environmental_chamber", "protocols": ["http"],
        "authentication": {"type": "none", "required_secrets": []},
        "documentation": [
            "examples/driver-creator/http-service/docs/chamberlink-openapi.yaml",
            "examples/driver-creator/http-service/docs/chamberlink-notes.md"],
    },
    "deebot": {
        "compatible": "ecovacs,deebot-n20",
        "id": "device://ecovacs/vacuum/deebot-n20",
        "vendor": "Ecovacs", "model": "Deebot N20",
        "category": "vacuum", "protocols": ["ecovacs-cloud"],
        # reached over a cloud transport needing an account login. Per the
        # registry doc: required_secrets are LOGICAL names, never values; the
        # SHAL env binding is recorded separately in secret_env.
        "authentication": {"type": "username_password",
                           "required_secrets": ["username", "password"],
                           "secret_env": {"username": "ECOVACS_EMAIL",
                                          "password": "ECOVACS_PASSWORD"},
                           "transport_compatible": "ecovacs,cloud-n20"},
        "documentation": [
            "examples/driver-creator/deebot/docs/deebot-protocol.md",
            "examples/driver-creator/deebot/docs/deebot-cloud-transport.md"],
    },
}


def _side_effect(ann: dict) -> str:
    if ann.get("readOnlyHint"):
        return "none"
    return "actuator" if ann.get("destructiveHint") else "write"


def _commands(cat_ops: list[dict]) -> tuple[list[dict], list[dict]]:
    """Catalog ops -> declarative commands + extracted safety constraints."""
    commands, safety = [], []
    for op in cat_ops:
        ann = op.get("annotations") or {}
        props = (op.get("input_schema") or {}).get("properties") or {}
        commands.append({
            "name": op["name"],
            "description": op.get("description"),
            "side_effect": _side_effect(ann),
            "idempotent": bool(ann.get("idempotentHint")),
            "unit": op.get("unit"),
            "parameters": props or None,
        })
        for pname, schema in props.items():
            bound = {k: schema[k] for k in
                     ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "enum")
                     if k in schema}
            if bound:
                safety.append({"command": op["name"], "parameter": pname,
                               "unit": op.get("unit"), **bound})
    return commands, safety


def emit(case: str) -> None:
    seed = _SEED[case]
    gen = ROOT / case / "generated"
    # importing the generated module(s) registers the driver. Evict the shared
    # module names first so a prior case's `driver` isn't returned from cache.
    sys.path.insert(0, str(gen))
    for mod in ("driver", "sim", "bus"):
        sys.modules.pop(mod, None)
        if (gen / f"{mod}.py").exists():
            __import__(mod)
    cat = shal.catalog(seed["compatible"])
    commands, safety = _commands(cat.get("ops") or [])

    device = {
        "id": seed["id"],
        "identity": {"vendor": seed["vendor"], "model": seed["model"],
                     "category": seed["category"]},
        "compatible": seed["compatible"],           # the SHAL driver binding id
        "capabilities": [cat.get("capability")] if cat.get("capability") else [],
        "transport": {"kind": cat.get("requires_parent_kind"),
                      "protocols": seed["protocols"],
                      "address_schema": cat.get("address_schema")},
        "authentication": seed["authentication"],
        "commands": commands,
        "safety_constraints": safety,
        "documentation": seed["documentation"],
    }
    metadata = {
        "id": seed["id"],
        "vendor": seed["vendor"], "model": seed["model"],
        "category": seed["category"], "protocols": seed["protocols"],
        "compatible": seed["compatible"],          # SHAL binding (links definition -> driver)
        "documentation": seed["documentation"],
        # ownership/trust block — authors/reviewers are top-level siblings of
        # verification, per the registry doc
        "authors": ["shal-generate-driver (AI)"],
        "reviewers": [],
        "verification": {
            "level": "generated",   # draft -> generated -> reviewed -> tested -> certified
            # provenance: passed shal.conformance + an independent harness at
            # generation time; the ladder's 'tested' level is reserved for registry CI
            "evidence": "passed shal.conformance and an independent harness",
        },
    }
    for name, doc in (("device.yaml", device), ("metadata.yaml", metadata)):
        # newline="\n": always emit LF so a Windows-authored manifest is byte-for-byte
        # identical to one CI re-emits on Linux (the "re-emit -> no diff" gate).
        (gen / name).write_text(
            yaml.safe_dump(doc, sort_keys=False, allow_unicode=True),
            encoding="utf-8", newline="\n")
    sys.path.remove(str(gen))
    print(f"  {case}: {seed['id']}  ({len(commands)} commands, "
          f"{len(safety)} safety constraints)")


def main() -> int:
    cases = sys.argv[1:] or list(_SEED)
    print("emitting registry manifests (device.yaml + metadata.yaml):")
    for case in cases:
        emit(case)
    return 0


if __name__ == "__main__":
    sys.exit(main())
