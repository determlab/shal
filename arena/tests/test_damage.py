"""issue #313: damage model rules (sourced limits, DMM fuse, replacement_usd home)."""
from __future__ import annotations

from pathlib import Path

import yaml

from shal_arena.card_sim import CardSim, load_instruments
from shal_arena.card_sim.model import Dmm, InstrumentSpec

REPO = Path(__file__).resolve().parents[2]


def _card(source_line: str) -> CardSim:
    doc = yaml.safe_load(
        "id: c\ninputs: {vin: {nominal_v: 5.0}}\n"
        "rails: {r: {from: vin, nominal_v: 5.0, test_point: tp}}\n"
        f"limits:\n  - {{input: vin, above_v: 6.0, effect: damage{source_line}}}\n"
        "faults: [{id: ok}]\n")
    return CardSim(doc)


def test_limit_without_source_does_not_cause_damage() -> None:
    sim = _card("")
    assert len(sim.ignored_limits) == 1
    sim.apply_input("vin", 30.0)
    assert sim.state == "ok" and sim.rail_voltage("r") == 5.0


def test_limit_with_blank_source_does_not_cause_damage() -> None:
    sim = _card(', source: "  "')
    sim.apply_input("vin", 30.0)
    assert sim.state == "ok"


def test_dmm_overcurrent_burns_fuse_then_reads_zero(tmp_path) -> None:
    spec = InstrumentSpec("dmm0", 1200, fuse_a=0.4, fuse_source="datasheet")
    dmm = Dmm(spec, log_path=tmp_path / "sim.log")
    assert dmm.measure_current(0.1) == 0.1
    assert dmm.measure_current(2.0) == 0.0
    assert dmm.state == "protection"
    assert dmm.measure_current(0.1) == 0.0
    assert "protection" in (tmp_path / "sim.log").read_text()


def test_catalogue_instrument_loads_replacement_usd() -> None:
    cat = REPO / "arena" / "src" / "shal_arena" / "card_sim" / "catalogue" / "instruments.yaml"
    assert load_instruments(cat)["dmm0"].replacement_usd == 1200


def test_replacement_usd_only_in_arena_yaml_never_in_shal_manifest() -> None:
    hits = []
    for p in REPO.rglob("*"):
        if (not p.is_file() or p.suffix not in {".yaml", ".yml"}
                or any(part in {".git", ".venv", "node_modules"} for part in p.parts)):
            continue
        if "replacement_usd" in p.read_text(encoding="utf-8", errors="ignore"):
            hits.append(p.relative_to(REPO))
    assert hits, "expected arena catalogue yaml to carry replacement_usd"
    assert all(h.parts[0] == "arena" for h in hits), hits
