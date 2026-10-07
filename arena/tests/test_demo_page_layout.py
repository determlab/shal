"""issue #447: the arena demo page, laid out to the approved layout and built
from the real run -- the result first (the measured line, the 4-step strip,
one live visual of the agent, its instruments and the card), the card's
block diagram drawn from the card yaml's own `blocks:` field, one card per
instrument, and the #457 log.

Every check renders a real recorded run (start, power, switch, measure,
answer -- the real runner, never a hand-built timeline) through the page's
own shipped `_SCRIPT`, in the same Node DOM stub the other UI tests use."""
from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

from shal_arena.loader import load_task
from shal_arena.runner import answer, call_op, drive_input, pick_fault, start_run, take_measurement
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import SAFETY_LINE, _shell, render_watch_page

from .conftest import (
    PASSING_DMM_DRIVER,
    PASSING_RELAY_DRIVER,
    PASSING_TEMP_DRIVER,
    RELAY_RAIL_TASK,
)
from .test_ui_export_ticks import _export_frames
from .test_ui_timeline_no_exchange import _render_with_node

_CARD_YAML = Path(RELAY_RAIL_TASK).parent.parent / "cards" / "buck-12v-relay.yaml"
_MOCKUP_LABEL = "Mockup · sample data · not a real run"
_REAL_LABELS = ("Replay of a recorded run", "medium level", "4 instruments",
                "datasheet written by us")
_TAG_RE = re.compile(r"<[^>]+>")


def _overheat_seed() -> int:
    card = load_task(str(RELAY_RAIL_TASK)).card
    return next(s for s in range(1, 500) if pick_fault(card, s) == "overheat")


def _recorded_run(tmp_path: Path, *, refuse_first: bool = False) -> tuple[str, Path]:
    """A real relay-rail run with the overheat fault, played through the
    real runner, then answered (closed)."""
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=_overheat_seed(),
                       state_dir=state_dir)["run_id"]
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "true"],
            state_dir=state_dir)
    if refuse_first:
        drive_input(run_id, "psu0", 30.0, state_dir=state_dir)   # refused by the gate
    drive_input(run_id, "psu0", 12.0, state_dir=state_dir)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)
    answer(run_id, "overheat", state_dir=state_dir)
    return run_id, state_dir


def _text(markup: str) -> str:
    """What a reader sees: tags dropped, entities decoded, spaces collapsed."""
    text = html.unescape(_TAG_RE.sub(" ", markup))
    return re.sub(r"\s+([,.])", r"\1", re.sub(r"\s+", " ", text)).strip()


def _page_text(elements: dict) -> str:
    return "\n".join(_text(v["html"]) for v in elements.values())


# --------------------------------------------------------------------------- #
# the top: one live visual, the result big; the measured line first
# --------------------------------------------------------------------------- #

def test_top_of_the_page_is_the_live_visual_with_the_result_big(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    hero = elements["hero"]["html"]
    assert ">Agent<" in hero and ">Card<" in hero
    for name in ("PSU", "DMM", "RELAY", "TEMP"):
        assert f">{name}<" in hero
    # agent -> instrument and instrument -> card, one arrow each, lit in
    # the order the agent used them (`--i`), the hot one marked bad
    arrows = re.findall(r'<path class="arrow ([a-z ]*)[^"]*"( style="--i:(\d+)")?', hero)
    assert len(arrows) == 8
    assert all("lit" in cls or "bad" in cls for cls, _, _ in arrows)
    assert sorted({int(i) for _, _, i in arrows}) == [0, 1, 2, 3]
    assert elements["hero-result"]["text"] == "overheat ✓"

    # everything else goes below it
    shell = _shell(run_id)
    hero_end = shell.index('id="hero-result"')
    for later in ('id="card-diagram"', 'id="instruments"', 'id="timeline-list"'):
        assert shell.index(later) > hero_end


def test_first_line_is_the_measured_value_and_its_limit_then_the_strip(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    bar = elements["verdict-bar"]["html"]
    first_line = _text(re.match(r'<div class="big">(.*?)</div>', bar).group(1))
    assert first_line == "Fault found: regulator overheating, 90.0 °C, limit 85 °C"
    assert "Agent's answer: overheat · Correct" in _text(bar)

    shell = _shell(run_id)
    hero = shell.index('class="hero"')
    assert hero < shell.index('id="verdict-bar"') < shell.index('id="flow"') < shell.index(
        'id="hero"')
    steps = re.findall(r'<div class="k">([^<]*)</div>', elements["flow"]["html"])
    assert steps == ["Gets", "Writes", "Measures", "Fault"]


# --------------------------------------------------------------------------- #
# the card: drawn from the card yaml's own blocks: field
# --------------------------------------------------------------------------- #

def test_card_yaml_has_the_block_field() -> None:
    doc = yaml.safe_load(_CARD_YAML.read_text(encoding="utf-8"))
    blocks = doc["blocks"]
    assert [b["label"] for b in blocks.values()] == ["VIN", "Relay", "Regulator", "3V3"]
    assert {b.get("test_point") for b in blocks.values()} >= {"tp_3v3", "tp_reg_temp"}


def test_the_diagram_is_drawn_from_the_block_field(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    diagram = _render_with_node(payload)["card-diagram"]["html"]
    for label in ("VIN", "Relay", "Regulator", "3V3"):
        assert f">{label}</text>" in diagram
    for point in ("tp_3v3", "tp_reg_temp"):          # each measure point, marked
        assert f">{point}</text>" in diagram
    assert diagram.count('<circle class="tp"') == 2
    # the instruments sit where the task's drives:/switches:/probe: say
    for addr in ("psu0", "relay0", "dmm0", "temp0"):
        assert f">{addr}</text>" in diagram
    # the regulator block is the hot one (90.0 °C over its 85 °C limit)
    assert re.search(r'<rect class="blk hot"[^>]*/><text[^>]*>Regulator<', diagram)

    # drawn from the data, not by hand: a renamed block is drawn renamed,
    # a dropped block is gone
    payload["card_blocks"] = [dict(b, label="Buck") if b["id"] == "regulator" else b
                              for b in payload["card_blocks"] if b["id"] != "relay"]
    changed = _render_with_node(payload)["card-diagram"]["html"]
    assert ">Buck</text>" in changed
    assert ">Regulator</text>" not in changed and ">Relay</text>" not in changed


def test_card_block_and_title_come_from_the_runs_own_card_yaml(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    loaded = load_task(str(RELAY_RAIL_TASK))
    assert payload["card_id"] == loaded.card.id == "buck-12v-relay"
    assert [b["id"] for b in payload["card_blocks"]] == [b.id for b in loaded.card.blocks]

    elements = _render_with_node(payload)
    assert elements["question"]["text"] == loaded.task.question.text
    assert "buck-12v-relay" in elements["card-spec"]["text"]
    assert "VIN 12 V" in elements["card-spec"]["text"]

    export = build_export(run_id, state_dir=state_dir)
    for text in (_page_text(elements), _page_text(_export_frames(payload)[-1]),
                 render_watch_page(run_id, payload), export):
        assert "28 V" not in text
        assert "28 V power module" not in text


# --------------------------------------------------------------------------- #
# one card per instrument, from the run timeline
# --------------------------------------------------------------------------- #

def test_one_instrument_card_per_instrument_with_functions_and_values(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    used = {e["address"] for e in payload["timeline"]}
    assert used == {"psu0", "relay0", "dmm0", "temp0"}

    cards = re.findall(r'<div class="inst[^"]*">(.*?)</div></div>',
                       _render_with_node(payload)["instruments"]["html"])
    assert len(cards) == len(used)
    by_addr = {re.search(r'inst-name">([a-z0-9]+) ', c).group(1): c for c in cards}
    assert set(by_addr) == used

    def rows(card: str) -> list[tuple[str, str]]:
        fn = re.search(r'<div class="fn">(.*)', card).group(1)
        cells = [html.unescape(c) for c in re.findall(r"<span[^>]*>([^<]*)</span>", fn)]
        return list(zip(cells[::2], cells[1::2], strict=True))

    reading = {e["address"]: e["detail"]["value"] for e in payload["timeline"]
               if e["kind"] == "reading"}
    assert rows(by_addr["psu0"]) == [("Powered the card at 12.00 V, through the SHAL gate",
                                      "12.00 V")]
    assert rows(by_addr["relay0"]) == [("set_relay(0, true)", "ok")]
    assert rows(by_addr["dmm0"]) == [("measure @tp_3v3", f"{reading['dmm0']:.2f} V")]
    assert rows(by_addr["temp0"]) == [("measure @tp_reg_temp", f"{reading['temp0']:.1f} °C")]
    # each card names its protocol, from the case's own bus
    for addr, proto in (("psu0", "SCPI"), ("dmm0", "SCPI"), ("relay0", "Modbus"),
                        ("temp0", "I2C")):
        assert f'inst-proto">{proto}<' in by_addr[addr]


def test_the_log_table_shows_every_bus_exchange_once(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)
    log = html.unescape(_render_with_node(payload)["timeline-list"]["html"])
    wires = re.findall(r'<div class="wire">([^<]*)</div>', log)
    assert "{fc:5, address:0, value:true} → {fc:5, address:0, value:true}" in wires
    scpi = next(e for e in payload["timeline"] if e["kind"] == "query")
    assert f"{scpi['detail']['cmd']} → {scpi['detail']['reply']}" in wires
    i2c = next(e for e in payload["timeline"] if e["kind"] == "exchange"
               and e["detail"]["bus_family"] == "sim_i2c")
    assert any(w.startswith("2C 06 → ") for w in wires)
    assert i2c["detail"]["request"] == "2c06"


# --------------------------------------------------------------------------- #
# the summary chip: a measured gate-stop count
# --------------------------------------------------------------------------- #

def test_summary_chip_says_gate_stops_counted_from_the_run(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path / "a")
    bar = _text(_render_with_node(run_payload(run_id, state_dir=state_dir))["verdict-bar"]["html"])
    assert "0 gate stops" in bar
    assert "unsafe commands" not in bar

    run_id, state_dir = _recorded_run(tmp_path / "b", refuse_first=True)
    bar = _text(_render_with_node(run_payload(run_id, state_dir=state_dir))["verdict-bar"]["html"])
    assert "1 gate stop" in bar and "1 gate stops" not in bar


# --------------------------------------------------------------------------- #
# labels: the mockup label is gone, the real-run labels are shown
# --------------------------------------------------------------------------- #

def test_mockup_label_absent_and_real_run_labels_present_live_and_export(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    payload = run_payload(run_id, state_dir=state_dir)

    live_html = render_watch_page(run_id, payload)
    export_html = build_export(run_id, state_dir=state_dir, agent="test-agent")
    for static in (live_html, export_html):
        assert _MOCKUP_LABEL not in static
        assert "Mockup" not in static and "not a real run" not in static

    live_labels = _text(_render_with_node(payload)["run-labels"]["html"])
    export_labels = _text(_export_frames(payload)[-1]["run-labels"]["html"])
    for labels in (live_labels, export_labels):
        for label in _REAL_LABELS:
            assert label in labels, (label, labels)

    # the footer: the safety line, then the replay label with agent, date and seed
    footer = export_html[export_html.index("<footer>"):export_html.index("</footer>")]
    assert SAFETY_LINE in footer
    assert re.search(r"Replay of a recorded run · test-agent, \d{4}-\d\d-\d\d, seed "
                     + str(payload["seed"]), footer)


def test_no_paragraph_text_beyond_the_title_line_result_and_footer(tmp_path: Path) -> None:
    run_id, state_dir = _recorded_run(tmp_path)
    shell = _shell(run_id)
    assert shell.count("<p") == 1 and 'class="lede" id="lede"' in shell
    lede = _render_with_node(run_payload(run_id, state_dir=state_dir))["lede"]["text"]
    assert lede == ("An AI agent got this card and four instruments it had never seen. "
                    "It wrote a driver for each one, measured, and named the fault.")
