"""issue #315 Done-when: the result card, its replay, and the rack page's
`setup.yaml` mechanism.

Every card/record/score file here comes from the real runner (`start_run`,
`check_instrument_driver`, `take_measurement`, `drive_input`, `answer`) —
never a hand-written fixture — so these tests exercise the same artifacts
`shal-arena replay` would write for an actual run."""
from __future__ import annotations

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
from shal_arena.replay.card import RunNotFinished, build_result_card, load_card_data
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
    assert names == {"scpi-psu", "dmm"}
    assert all(t.has_driver for t in tiles)  # both packaged cases ship a sim


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
