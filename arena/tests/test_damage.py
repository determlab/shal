"""issue #313: damage model rules (sourced limits, DMM fuse, replacement_usd home)."""
from __future__ import annotations

from pathlib import Path

import yaml

from shal_arena.card_sim import CardSim, load_instruments
from shal_arena.card_sim.model import Dmm, InstrumentSpec

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

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
    assert load_instruments(cat)["dmm"].replacement_usd == 1200


def test_catalogue_is_keyed_by_case() -> None:
    from shal_arena.card_sim import catalogue
    from shal_arena.cases import resolve_case

    cat = catalogue()
    assert {"dmm", "scpi-psu"} <= set(cat)
    for case in cat:
        assert resolve_case(case).name == case


def _seed_not_open() -> int:
    from shal_arena.loader import load_task
    from shal_arena.runner import pick_fault

    card = load_task(SAMPLE_TASK).card
    return next(s for s in range(100) if pick_fault(card, s) != "open")


def test_measurement_after_damage_is_not_healthy_and_shows_card_state(tmp_path) -> None:
    from shal_arena.runner import drive_input, start_run, take_measurement

    run_id = start_run(str(SAMPLE_TASK), seed=_seed_not_open(), state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 6.5, state_dir=tmp_path)  # over the 6.0 V abs max
    out = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    assert out["reading"] == 0.0
    assert out["card"]["state"] == "damage"
    assert out["card"]["supply_a"] == 10.0
    assert out["current_a"] == 0.0 and out["fuse"] == "protection"  # 10 A burns the fuse


def test_measurement_on_healthy_card_reads_healthy(tmp_path) -> None:
    from shal_arena.runner import start_run, take_measurement

    run_id = start_run(str(SAMPLE_TASK), seed=_seed_not_open(), state_dir=tmp_path)["run_id"]
    out = take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    assert out["card"]["state"] == "ok"
    assert out["reading"] != 0.0
    assert out["current_a"] == 0.05


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
