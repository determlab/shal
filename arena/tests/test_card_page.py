"""issue #315 Done-when: the result card, its replay, and the rack page's
`setup.yaml` mechanism.

Every card/record/score file here comes from the real runner (`start_run`,
`check_instrument_driver`, `take_measurement`, `drive_input`, `answer`) —
never a hand-written fixture — so these tests exercise the same artifacts
`shal-arena replay` would write for an actual run."""
from __future__ import annotations

import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import shal
import yaml

from shal_arena.errors import ArenaError, CheckCouldNotRun, MeasurementFailed
from shal_arena.replay.card import (
    RunNotFinished,
    _headline,
    build_result_card,
    load_card_data,
)
from shal_arena.replay.rack import (
    _JS_BUILD_SETUP_YAML,
    build_setup_yaml,
    rack_tiles,
    render_rack_page,
)
from shal_arena.runner import (
    answer,
    check_instrument_driver,
    drive_input,
    start_run,
    take_measurement,
)
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, PASSING_DRIVER, SAMPLE_TASK

_FAULT_WORDS = ("low_voltage", "noise", "unreachable", "shift_v", "ripple_vpp")


def _play_full_run(tmp_path: Path) -> tuple[str, Path]:
    """One real, closed run: two `check` calls (one repeated), one `measure`,
    two `drive` calls (one on an unknown address, which still costs its turn
    — issue #325/#314's ruling), then `answer`. Returns ``(run_id,
    state_dir)``."""
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]

    check_instrument_driver(run_id, "psu0", PASSING_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)  # re-check
    try:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    except MeasurementFailed:
        pass  # the 'open' fault: a live failure, still a turn, never persisted

    drive_input(run_id, "psu0", 5.0, state_dir=state_dir)
    with pytest.raises(CheckCouldNotRun):  # unknown address: fails, but already counted
        drive_input(run_id, "no-such-address", 5.0, state_dir=state_dir)

    answer(run_id, "ok", state_dir=state_dir)
    return run_id, state_dir


# --------------------------------------------------------------------------- #
# DoD: `pytest arena/tests/test_card_page.py -q` passes on a real sample
# record and score file from the runner.
# --------------------------------------------------------------------------- #

def test_card_builds_from_a_real_run(tmp_path: Path) -> None:
    run_id, state_dir = _play_full_run(tmp_path)

    card_html = build_result_card(run_id, state_dir=state_dir)

    assert "<!doctype html>" in card_html.lower()
    assert "Replay" in card_html
    store = RunStore(state_dir)
    score = yaml.safe_load(store.score_path(run_id).read_text(encoding="utf-8"))
    assert str(score["turns"]) in card_html


# --------------------------------------------------------------------------- #
# DoD: the turn count shown on the card equals the record's
# `check`+`measure`+`drive` calls (a refused `drive` counted, `answer` not).
# --------------------------------------------------------------------------- #

def test_turn_count_on_the_card_matches_check_measure_drive_calls(tmp_path: Path) -> None:
    run_id, state_dir = _play_full_run(tmp_path)
    # 3 check + 1 measure + 2 drive (the second raises, but still counted) = 6.
    # `answer` itself is never counted.
    expected_turns = 6

    data = load_card_data(run_id, state_dir=state_dir)
    assert data.score["turns"] == expected_turns

    card_html = build_result_card(run_id, state_dir=state_dir)
    assert f"turns {expected_turns}" in card_html


def test_a_refused_drive_still_counts_its_turn_directly_on_the_store(tmp_path: Path) -> None:
    """The same CTO ruling (#314), isolated: `store.increment_turns` runs
    before the drive's own outcome is known, so a `drive` call that goes on
    to fail/refuse is still one turn — not zero."""
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]

    with pytest.raises(ArenaError):
        drive_input(run_id, "unknown-addr", 1.0, state_dir=state_dir)

    state = RunStore(state_dir).load(run_id)
    assert state.turns == 1


# --------------------------------------------------------------------------- #
# issue #358: singular/plural `false fail(s)` wording on the headline.
# --------------------------------------------------------------------------- #

def test_headline_false_fails_is_singular_only_at_one() -> None:
    base = {"faults_caught": 1, "faults_total": 3}
    assert _headline({**base, "false_fails": 0}) == "1 of 3 faults caught. 0 false fails."
    assert _headline({**base, "false_fails": 1}) == "1 of 3 faults caught. 1 false fail."
    assert _headline({**base, "false_fails": 2}) == "1 of 3 faults caught. 2 false fails."


def test_headline_with_no_fault_never_says_0_of_0() -> None:
    """issue #495: a good card (`broken_probe`) has no card fault to catch."""
    base = {"faults_caught": 0, "faults_total": 0}
    assert _headline({**base, "false_fails": 0}) == "No fault was injected. 0 false fails."
    assert _headline({**base, "false_fails": 1}) == "No fault was injected. 1 false fail."


def test_card_for_a_seed_with_no_faults_says_so(tmp_path: Path) -> None:
    from .conftest import RELAY_RAIL_TASK
    state_dir = tmp_path / "state"
    run_id = start_run(str(RELAY_RAIL_TASK), seed=5, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=state_dir)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    answer(run_id, "open", state_dir=state_dir)   # blames the card: a false fail
    card_html = build_result_card(run_id, state_dir=state_dir)
    assert "0 of 0" not in card_html
    assert "No fault was injected." in card_html
    assert html.escape("The agent's answer: open. Wrong: the card is fine, the probe is "
                       "broken. This is a false fail.") in card_html


# --------------------------------------------------------------------------- #
# issue #358: the safety line sits under the existing bottom line.
# --------------------------------------------------------------------------- #

def test_card_html_has_the_safety_line_and_keeps_the_bottom_line(tmp_path: Path) -> None:
    run_id, state_dir = _play_full_run(tmp_path)
    card_html = build_result_card(run_id, state_dir=state_dir)

    assert "Simulated instruments only. Nothing here touches real hardware." in card_html
    assert "replay · simulated · seed" in card_html


# --------------------------------------------------------------------------- #
# issue #358: the gate-stop sentence appears next to a refused drive, and
# only there — never for a bad-argument drive error.
# --------------------------------------------------------------------------- #

def test_gate_refused_drive_shows_the_stop_sentence_on_the_card(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]

    # 6.5 V on psu0 breaches buck-5v-3v3's 6.0 V abs-max (damage); the SHAL
    # gate (issue #330) refuses it before anything is applied.
    refusal = drive_input(run_id, "psu0", 6.5, state_dir=state_dir)
    assert refusal["sent"] is False and refusal["rejected"] == "limits"

    answer(run_id, "ok", state_dir=state_dir)
    card_html = build_result_card(run_id, state_dir=state_dir)

    assert "drive — refused" in card_html
    assert "Stopped by the gate before it ran. Nothing was changed." in card_html


def test_bad_argument_drive_never_shows_the_gate_stop_sentence(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]

    with pytest.raises(CheckCouldNotRun):  # bad address: raises before any sim-log write
        drive_input(run_id, "no-such-address", 5.0, state_dir=state_dir)

    answer(run_id, "ok", state_dir=state_dir)
    card_html = build_result_card(run_id, state_dir=state_dir)

    assert "Stopped by the gate before it ran. Nothing was changed." not in card_html


# --------------------------------------------------------------------------- #
# DoD: `grep -n "http" <card html>` finds only the `github.com/determlab/shal`
# link; the page loads with the network off.
# --------------------------------------------------------------------------- #

def test_card_html_has_exactly_one_http_reference_and_no_external_resources(
        tmp_path: Path) -> None:
    run_id, state_dir = _play_full_run(tmp_path)
    card_html = build_result_card(run_id, state_dir=state_dir)

    http_lines = [line for line in card_html.splitlines() if "http" in line]
    assert http_lines, "expected the repo link"
    assert all("github.com/determlab/shal" in line for line in http_lines), http_lines

    # offline: no external stylesheet, font, or script source anywhere.
    assert "<link" not in card_html
    assert re.search(r'<script[^>]+src=', card_html) is None
    assert "fonts.googleapis.com" not in card_html
    assert "cdn." not in card_html


# --------------------------------------------------------------------------- #
# DoD: the card html before run end contains no fault name.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("stage", ["started", "checked", "measured"])
def test_card_before_run_end_raises_and_never_names_the_fault(stage: str, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    result = start_run(SAMPLE_TASK, state_dir=state_dir)
    run_id = result["run_id"]

    if stage in ("checked", "measured"):
        check_instrument_driver(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    if stage == "measured":
        try:
            take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
        except MeasurementFailed:
            pass

    with pytest.raises(RunNotFinished):
        build_result_card(run_id, state_dir=state_dir)

    # no card html exists at all before the run closes — the whole state dir
    # is clean of every fault-specific word (same leak scan as
    # test_scoring.py's run-folder test, applied to this ticket's new files).
    for path in state_dir.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not any(w in text for w in _FAULT_WORDS), (path, text)


def test_card_template_source_has_no_fault_vocabulary() -> None:
    """A static, independent check on the generator itself: the template
    strings it is built from never hardcode a fault id or fault-specific
    word, so no future edit can leak one into a card before a run ends."""
    source = Path(__file__).resolve().parents[1] / "src" / "shal_arena" / "replay" / "card.py"
    text = source.read_text(encoding="utf-8")
    # score["fault_type"] is read generically (a variable), never one of the
    # concrete fault ids written as a literal in this module's own source.
    for word in ("low_voltage", "noise_vpp", "ripple_vpp", "shift_v"):
        assert word not in text


# --------------------------------------------------------------------------- #
# DoD: the generated `setup.yaml` loads in `shal` and passes validation.
# --------------------------------------------------------------------------- #

def test_rack_tiles_cover_the_packaged_cases() -> None:
    tiles = rack_tiles()
    names = {t.case for t in tiles}
    assert names == {"scpi-psu", "dmm", "relay-modbus", "sht31"}
    assert all(t.has_driver for t in tiles)  # every packaged case ships a sim


def test_generated_setup_yaml_loads_in_shal_and_validates() -> None:
    doc_text = build_setup_yaml(["scpi-psu", "dmm"])
    doc = yaml.safe_load(doc_text)

    assert doc["shal_version"] == 1
    hal = shal.load(doc)
    try:
        schemas = hal.tool_schemas()
        assert schemas  # the two instruments bound to real, callable tools
    finally:
        hal.close()


def test_generated_setup_yaml_single_tile_also_loads() -> None:
    doc = yaml.safe_load(build_setup_yaml(["dmm"]))
    hal = shal.load(doc)
    hal.close()


def test_build_setup_yaml_rejects_an_unknown_case() -> None:
    with pytest.raises(ArenaError):
        build_setup_yaml(["not-a-real-case"])


def test_build_setup_yaml_rejects_an_empty_rack() -> None:
    with pytest.raises(ValueError):
        build_setup_yaml([])


def test_rack_page_js_builder_matches_the_python_builder(tmp_path: Path) -> None:
    """CTO review on #327: the page's embedded `_JS_BUILD_SETUP_YAML` is a
    second, hand-written copy of `build_setup_yaml` (one for a browser drag,
    one for a pytest/CLI call) — this pins the two together so a future edit
    to either one that drifts from the other fails here, not in a browser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")

    case_names = ["dmm", "scpi-psu"]
    compat_by_case = {t.case: t.compatible for t in rack_tiles()}
    script = tmp_path / "build.mjs"
    script.write_text(
        _JS_BUILD_SETUP_YAML
        + "\nconsole.log(buildSetupYaml(" + json.dumps(case_names) + ", "
        + json.dumps(compat_by_case) + "));\n",
        encoding="utf-8")

    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr

    assert yaml.safe_load(proc.stdout) == yaml.safe_load(build_setup_yaml(case_names))


_DOM_STUB = """
function mkEl(props) {
  const cls = new Set();
  const attrs = {};
  const handlers = {};
  return Object.assign({
    dataset: {}, textContent: "", value: "", id: "", handlers, attrs,
    classList: {add: c => cls.add(c), remove: c => cls.delete(c),
                contains: c => cls.has(c)},
    setAttribute: (k, v) => { attrs[k] = v; },
    addEventListener: (ev, fn) => { handlers[ev] = fn; },
  }, props);
}
const fakeTiles = TILES.map(c => {
  const el = mkEl({dataset: {case: c}});
  el.attrs["aria-pressed"] = "false";
  return el;
});
const fakeSlots = [0, 1, 2, 3].map(i => mkEl({dataset: {slot: String(i)}}));
const notHere = mkEl({id: "not-here", dataset: {slot: "4"}});
const byId = {"yaml-out": mkEl({}), "not-here": notHere,
              "download-btn": mkEl({}), "copy-btn": mkEl({})};
globalThis.document = {
  querySelectorAll: sel => sel.startsWith(".slot") ? [...fakeSlots, notHere] : fakeTiles,
  getElementById: id => byId[id],
};
globalThis.window = {open: () => {}};
"""

_DOM_DRIVER = """
const out = [];
for (const [mode, name, i] of PLAN) {
  const tile = fakeTiles.find(t => t.dataset.case === name);
  if (mode === "tap") {
    tile.handlers.click();
    out.push(tile.attrs["aria-pressed"] + ":" + tile.classList.contains("selected"));
    fakeSlots[i].handlers.click();
    out.push(tile.attrs["aria-pressed"] + ":" + tile.classList.contains("selected"));
  } else {
    fakeSlots[i].handlers.drop({preventDefault() {},
      dataTransfer: {getData: () => name}});
  }
}
console.log(JSON.stringify({yaml: byId["yaml-out"].value, states: out,
                            labels: fakeSlots.map(s => s.textContent)}));
"""


def _run_rack_page(tmp_path: Path, plan: list[list]) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    page = render_rack_page()
    page_script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    script = tmp_path / "page.mjs"
    script.write_text(
        "const TILES = " + json.dumps([t.case for t in rack_tiles()]) + ";\n"
        "const PLAN = " + json.dumps(plan) + ";\n"
        + _DOM_STUB + page_script + _DOM_DRIVER, encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_rack_page_tap_path_and_drag_path_build_the_same_setup_yaml(tmp_path: Path) -> None:
    # scpi-bus cases only: the page's JS builder hard-codes sim-scpi/address 1
    # (the pre-existing drift for sht31/relay-modbus is outside this change).
    cases = ["dmm", "scpi-psu", "dmm"]
    tap = _run_rack_page(tmp_path, [["tap", c, i] for i, c in enumerate(cases)])
    drag = _run_rack_page(tmp_path, [["drag", c, i] for i, c in enumerate(cases)])

    assert tap["yaml"] == drag["yaml"]
    assert tap["labels"] == drag["labels"]
    assert yaml.safe_load(tap["yaml"]) == yaml.safe_load(build_setup_yaml(cases))
    # Tap selects (aria-pressed true), placing clears the selection.
    assert tap["states"][:2] == ["true:true", "false:false"]


def test_rack_page_tapping_a_selected_tile_again_unselects_it(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    page = render_rack_page()
    page_script = page.split("<script>", 1)[1].split("</script>", 1)[0]
    script = tmp_path / "toggle.mjs"
    script.write_text(
        "const TILES = " + json.dumps([t.case for t in rack_tiles()]) + ";\n"
        + _DOM_STUB + page_script
        + """
const t = fakeTiles[0];
t.handlers.click();
const on = t.attrs["aria-pressed"] + ":" + t.classList.contains("selected");
t.handlers.click();
fakeSlots[0].handlers.click();  // nothing selected: nothing placed
console.log(on, t.attrs["aria-pressed"], t.classList.contains("selected"),
            JSON.stringify(byId["yaml-out"].value));
""", encoding="utf-8")
    proc = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == 'true:true false false ""'


def test_rack_page_has_tap_handler_note_and_only_the_github_link() -> None:
    page = render_rack_page()
    assert "addEventListener('click'" in page
    assert 'aria-pressed="false"' in page
    assert "Drag and tap both work" in page
    assert "dragstart" in page and "'drop'" in page  # drag-and-drop stays
    http_lines = [ln for ln in page.splitlines() if "http" in ln]
    assert http_lines
    assert all("github.com/determlab/shal" in ln for ln in http_lines)
    assert "https://" not in page.replace("https://github.com/determlab/shal", "")


def test_setup_yaml_output_is_unchanged_byte_for_byte() -> None:
    proc = _run_cli("setup-yaml", "dmm", "scpi-psu", "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["setup_yaml"] == build_setup_yaml(["dmm", "scpi-psu"])
    assert build_setup_yaml(["dmm"]).startswith("shal_version: 1\nroot:\n  bench0:\n")


def test_rack_page_renders_offline_and_has_both_tiles() -> None:
    page = render_rack_page()
    assert "<!doctype html>" in page.lower()
    assert "scpi-psu" in page
    assert "dmm" in page
    assert "My instrument is not here" in page
    assert "<link" not in page  # no external stylesheet/font


# --------------------------------------------------------------------------- #
# CLI roundtrip (the Agent path: `shal-arena replay`/`rack`/`setup-yaml`).
# --------------------------------------------------------------------------- #

def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def test_cli_replay_writes_the_card_after_answer(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]
    _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
             "--state-dir", str(state_dir), "--json")
    _run_cli("answer", run_id, "ok", "--state-dir", str(state_dir), "--json")

    out_path = tmp_path / "card.html"
    proc = _run_cli("replay", run_id, "--state-dir", str(state_dir),
                    "--out", str(out_path), "--json")
    assert proc.returncode == 0, proc.stderr
    assert out_path.is_file()
    assert "Replay" in out_path.read_text(encoding="utf-8")


def test_cli_replay_before_answer_refuses(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]

    proc = _run_cli("replay", run_id, "--state-dir", str(state_dir), "--json")
    assert proc.returncode == 2
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False
    assert doc["error"]["fix"]


def test_cli_setup_yaml_matches_the_python_builder(tmp_path: Path) -> None:
    proc = _run_cli("setup-yaml", "dmm", "scpi-psu", "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert yaml.safe_load(doc["setup_yaml"]) == yaml.safe_load(
        build_setup_yaml(["dmm", "scpi-psu"]))
    assert doc["side_effect"] == "none"  # no --out: nothing written to disk


def test_cli_replay_and_rack_json_declare_their_side_effect(tmp_path: Path) -> None:
    """CTO review on #327: every action declares its side effect — `replay`,
    `rack` and `setup-yaml --out` all write a file, so their `--json` must
    say `"side_effect": "write"`, not leave it out."""
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]
    _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
             "--state-dir", str(state_dir), "--json")
    _run_cli("answer", run_id, "ok", "--state-dir", str(state_dir), "--json")

    replay_proc = _run_cli("replay", run_id, "--state-dir", str(state_dir),
                           "--out", str(tmp_path / "card.html"), "--json")
    assert json.loads(replay_proc.stdout)["side_effect"] == "write"

    rack_proc = _run_cli("rack", "--out", str(tmp_path / "rack.html"), "--json")
    assert json.loads(rack_proc.stdout)["side_effect"] == "write"

    setup_proc = _run_cli("setup-yaml", "dmm", "--out", str(tmp_path / "setup.yaml"), "--json")
    assert json.loads(setup_proc.stdout)["side_effect"] == "write"
