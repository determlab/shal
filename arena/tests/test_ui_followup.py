"""issue #427 (demo follow-up to #406): the CMO problem text is on every
page, `drivers` round-trips into the embedded payload and is never
interpreted as markup, `rails`/`level` are in the payload for the answer
sentence, and the static shell has the separate containers the
"Two more checks, scripted" section and the driver-code fold need."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.simlog import SimLog
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import PROBLEM_LINES, render_watch_page

from .conftest import PASSING_DMM_DRIVER, PASSING_TEMP_DRIVER, RELAY_RAIL_TASK, SAMPLE_TASK

_BASE_PAYLOAD = {
    "run_id": "run-x", "task_id": "t", "title": "T", "question": "Q",
    "level": "easy", "status": "open", "closed": False, "turns": 0,
    "instruments": [{"address": "psu0", "case": None, "drives": "card.vin", "probe": None},
                     {"address": "dmm0", "case": None, "drives": None, "probe": "card.tp_3v3"}],
    "rails": [{"label": "tp_3v3", "nominal_v": 3.3, "tol_pct": 5, "lo": 3.135, "hi": 3.465}],
    "tiles": {}, "card": {"applied": {}, "destroyed": False},
    "timeline": [], "record": None, "score": None, "drivers": {},
}


def test_problem_lines_are_on_the_static_shell() -> None:
    html = render_watch_page("run-x", _BASE_PAYLOAD)
    for line in PROBLEM_LINES:
        assert line in html


_STEP_CAPTIONS = (
    "The agent reads each instrument's datasheet.",
    "It writes a driver for each one and checks it.",
    "The bench: power supply, multimeter, card.",
    "It powers the card and measures the 3.3 V rail.",
    "A scripted step asks for 30 V on purpose. The gate stops it. Nothing was sent.",
    "A cable is unplugged. The result is error, not fail: the card is not blamed.",
    "The answer: which measurement failed, against which limit.",
)


def test_all_seven_step_captions_are_verbatim_on_the_page() -> None:
    """issue #427 CTO review round 2: captions 1, 2, 4, 6 and 7 were
    missing. All seven, exact text, from the #406 issue body."""
    html = render_watch_page("run-x", _BASE_PAYLOAD)
    for caption in _STEP_CAPTIONS:
        assert caption in html, caption


def test_shell_has_the_driver_code_and_scripted_containers() -> None:
    html = render_watch_page("run-x", _BASE_PAYLOAD)
    assert '<div id="driver-code-section">' in html
    assert '<div id="scripted-section">' in html
    assert '<div id="plain-line" class="plain-line">' in html


def test_payload_carries_level_and_rails_for_the_answer_sentence() -> None:
    payload = dict(_BASE_PAYLOAD)
    html = render_watch_page("run-x", payload)
    assert '"level": "easy"' in html or '"level":"easy"' in html
    assert "tp_3v3" in html


def test_a_hostile_driver_code_cannot_break_out_of_the_script_tag() -> None:
    """The driver's own source is arbitrary text an agent wrote -- it is
    rendered client side with `textContent` (never `innerHTML`), but the
    embedded JSON itself must still survive a `</script>` breakout attempt,
    same as a hostile `record.given` (test_ui_api.py's own check)."""
    payload = dict(_BASE_PAYLOAD)
    hostile = "def read():\n    pass  # </script><script>window.pwned=1</script>"
    payload["drivers"] = {"dmm": {"lines": 2, "code": hostile}}
    html = render_watch_page("run-x", payload)
    assert "</script><script>window.pwned" not in html
    assert "\\u003c/script>\\u003cscript>window.pwned" in html
    assert html.count("<script") == 2
    assert html.count("</script>") == 2


def test_a_hostile_driver_code_is_never_executed_as_html_on_export() -> None:
    payload = dict(_BASE_PAYLOAD)
    payload["closed"] = True
    payload["status"] = "closed"
    payload["record"] = {"given": "ok", "correct": True, "disqualified": False}
    hostile = "<img src=x onerror=alert(1)>"
    payload["drivers"] = {"psu": {"lines": 1, "code": hostile}}
    from shal_arena.ui.export import render_export_page

    html = render_export_page(payload)
    assert "<img src=x onerror" not in html


def test_reading_kind_is_logged_only_on_a_successful_read_and_shows_up_in_the_timeline(
        tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    from shal_arena.ui.data import run_payload
    payload = run_payload(run_id, state_dir=tmp_path)
    readings = [e for e in payload["timeline"] if e["kind"] == "reading"]
    assert len(readings) == 1
    assert readings[0]["address"] == "dmm0"
    assert isinstance(readings[0]["detail"]["value"], float)


def test_drivers_round_trip_through_the_export(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    drivers = {"dmm": {"lines": 3, "code": "def read():\n    return 3.3\n"}}
    html = build_export(run_id, state_dir=tmp_path, drivers=drivers)
    assert "def read():" in html
    assert '"dmm"' in html


# --------------------------------------------------------------------------- #
# #427 CTO review round 2: the closing sentence, built server side
# --------------------------------------------------------------------------- #

def test_mark_reading_rejects_a_non_numeric_value(tmp_path: Path) -> None:
    """CTO blocker: a driver's own code decides what it returns -- this is
    the one place that decides what counts as "a reading" at all. Anything
    that is not a bare number must never reach the sim log as one."""
    log = SimLog(tmp_path / "run.simlog.jsonl")
    with pytest.raises((TypeError, ValueError)):
        log.mark_reading("dmm0", "<img src=x onerror=alert(1)>", "volt")  # type: ignore[arg-type]


def test_mark_reading_rejects_nan_and_infinity(tmp_path: Path) -> None:
    """CTO review round 2: `float("nan")` would log as the bare JSON token
    `NaN`, which is not valid JSON -- `JSON.parse` on the page throws and
    the whole page renders empty. Reject it at the source, same as any
    other non-finite value."""
    log = SimLog(tmp_path / "run.simlog.jsonl")
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            log.mark_reading("dmm0", bad, "volt")
    assert not (tmp_path / "run.simlog.jsonl").exists()


def test_answer_label_never_says_faulty(tmp_path: Path) -> None:
    """CTO review round 2: drop the "faulty," prefix -- the fault's own
    name already says that."""
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next(s for s in range(200)
               if fault_mod.realized_fault(card, s).fault_id == "low_voltage")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "low_voltage", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "The agent's answer: low voltage." in sentence
    assert "faulty" not in sentence


def test_answer_sentence_covers_the_noise_fault(tmp_path: Path) -> None:
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next((s for s in range(200)
                if fault_mod.realized_fault(card, s).fault_id == "noise"), None)
    if seed is None:
        pytest.skip("rail-3v3 has no 'noise' fault in its own faults list")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "noise", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert sentence is not None
    assert "the 3V3 rail reads" in sentence
    assert "The agent's answer: noise." in sentence


def test_answer_sentence_names_the_range_across_repeated_noisy_reads(tmp_path: Path) -> None:
    """CTO review round 3: the noise fault can give a different reading on
    every read -- the sentence must name the range actually seen, not pick
    one arbitrary value. This sim's own ripple is deterministic per run
    (same seed, same shift every call), so the varying-value case is built
    directly from the sim log, the same shape a real noisy instrument
    would leave."""
    run_id = start_run(str(SAMPLE_TASK), seed=2, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)

    log_path = tmp_path / f"{run_id}.simlog.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        for i, value in enumerate((3.25, 3.38, 3.31)):
            f.write(json.dumps({"ts": f"2026-10-06T13:25:2{i}Z", "address": "dmm0",
                                "kind": "reading", "value": value, "unit": "volt"}) + "\n")
    answer(run_id, "noise", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "reads 3.25-3.38 V across reads" in sentence


def test_answer_sentence_on_a_destroyed_card_after_a_real_reading(tmp_path: Path) -> None:
    """CTO review round 2: "destroyed" must not say "before any reading"
    when a reading was in fact taken first."""
    from shal_arena.runner import raw_scpi

    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)  # a real reading
    raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=tmp_path)  # then destroys the card
    answer(run_id, "ok", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "before any reading" not in sentence
    assert "rail reads" in sentence


def test_no_answer_only_when_a_read_really_failed_not_just_absent(tmp_path: Path) -> None:
    """CTO review round 2: an old capture with no `reading` line at all
    (never measured) must leave that instrument out of the sentence
    entirely -- "No answer from the DMM" is reserved for a real failed
    attempt (the `open` fault)."""
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    answer(run_id, "ok", state_dir=tmp_path)  # closed with no measurement at all

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert sentence is not None
    assert "No answer" not in sentence
    assert sentence == "The agent's answer: ok. Wrong: the card has a fault."


def test_a_hostile_driver_reading_fails_the_measurement_instead_of_being_logged(
        tmp_path: Path) -> None:
    """End to end: a driver whose read op returns a non-numeric value never
    produces a `reading` sim-log entry -- `take_measurement` raises instead,
    same as any other driver bug, so the hostile value can never reach a
    rendered page at all. Monkeypatches `mark_reading` itself (not the
    driver source) -- it is the one call site whose return value stands in
    for "whatever a hostile driver returned"."""
    from shal_arena.errors import MeasurementFailed

    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)

    original = SimLog.mark_reading

    def hostile_mark_reading(self, address, value, unit):  # noqa: ANN001
        return original(self, address, "<img src=x onerror=alert(1)>", unit)

    SimLog.mark_reading = hostile_mark_reading
    try:
        with pytest.raises(MeasurementFailed):
            take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    finally:
        SimLog.mark_reading = original

    entries = SimLog(tmp_path / f"{run_id}.simlog.jsonl").entries()
    assert all(e["kind"] != "reading" for e in entries)


def test_answer_sentence_on_a_correct_in_spec_answer(tmp_path: Path) -> None:
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next(s for s in range(200) if fault_mod.realized_fault(card, s).fault_id == "ok")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert sentence is not None
    assert sentence.startswith("Measured: the ")
    assert "window" in sentence
    assert "The agent's answer: ok." in sentence
    assert sentence.endswith("Correct.")


def test_answer_sentence_on_a_wrong_answer_names_the_real_fault(tmp_path: Path) -> None:
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next(s for s in range(200)
               if fault_mod.realized_fault(card, s).fault_id == "low_voltage")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)  # wrong: it IS faulty

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "The agent's answer: ok." in sentence
    assert sentence.endswith("Wrong: the card has a fault.")


def test_answer_sentence_covers_the_open_fault_with_no_reading(tmp_path: Path) -> None:
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next(s for s in range(200)
               if fault_mod.realized_fault(card, s).fault_id == "open")
    from shal_arena.errors import MeasurementFailed

    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    with pytest.raises(MeasurementFailed):  # open means no answer at all
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "open", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "No answer from the DMM" in sentence
    assert sentence.endswith("Correct.")


def test_a_destroyed_card_never_says_correct_even_if_the_answer_would_match(
        tmp_path: Path) -> None:
    """CTO review round 3 (blocker): seed 2 realizes 'ok' -- measuring
    first would make 'ok' the technically correct answer, but the card is
    destroyed right after, and the sentence must say so, never 'Correct'."""
    run_id = start_run(str(SAMPLE_TASK), seed=2, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    from shal_arena.runner import raw_scpi
    raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    assert payload["card"]["destroyed"] is True
    sentence = payload["answer_sentence"]
    assert "Correct" not in sentence
    assert sentence.endswith("Wrong: the card is destroyed.")


def test_a_successful_drive_is_its_own_timeline_step(tmp_path: Path) -> None:
    """CTO review round 3: a clean, in-range drive used to write nothing
    to the sim log at all -- the Watch timeline had no step explaining why
    the PSU box's value changed. Now it logs a `write` entry."""
    run_id = start_run(str(SAMPLE_TASK), seed=2, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    writes = [e for e in payload["timeline"]
             if e["kind"] == "write" and e["address"] == "psu0"]
    assert len(writes) == 1
    assert writes[0]["detail"]["volts"] == 5.0


def test_a_query_without_a_reading_says_the_driver_failed_not_no_answer(
        tmp_path: Path) -> None:
    """CTO review round 3: an old capture (the adk-lab shape, from before
    the `reading` sim-log kind existed) has `measure` + `query` entries
    but no `reading` line -- the exchange worked, so this is never "No
    answer"."""
    run_id = start_run(str(SAMPLE_TASK), seed=2, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)

    log_path = tmp_path / f"{run_id}.simlog.jsonl"
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": "2026-10-06T13:25:20Z", "address": "dmm0",
                            "kind": "measure"}) + "\n")
        f.write(json.dumps({"ts": "2026-10-06T13:25:21Z", "address": "dmm0",
                            "kind": "query", "cmd": "MEAS:VOLT:DC?"}) + "\n")
    answer(run_id, "ok", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "No answer" not in sentence
    assert "the agent's driver failed to read the DMM" in sentence


def test_a_measure_with_no_query_at_all_is_a_real_no_answer(tmp_path: Path) -> None:
    """The `open` fault: the read never reaches the bus at all -- only the
    neutral `measure` marker is logged. This is the one real failure."""
    from shal_arena import fault as fault_mod
    from shal_arena.errors import MeasurementFailed
    from shal_arena.loader import load_task

    card = load_task(str(SAMPLE_TASK)).card
    seed = next(s for s in range(200)
               if fault_mod.realized_fault(card, s).fault_id == "open")
    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    with pytest.raises(MeasurementFailed):
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "open", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    entries = [e for e in payload["timeline"] if e["address"] == "dmm0"]
    assert not any(e["kind"] == "query" for e in entries)
    sentence = payload["answer_sentence"]
    assert "No answer from the DMM" in sentence


def test_answer_sentence_on_a_destroyed_card(tmp_path: Path) -> None:
    from shal_arena.runner import raw_scpi

    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    raw_scpi(run_id, "psu0", "VOLT 30.0", state_dir=tmp_path)  # no gate -- destroys the card
    answer(run_id, "ok", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    assert payload["card"]["destroyed"] is True
    sentence = payload["answer_sentence"]
    assert sentence is not None
    assert "destroyed" in sentence


def test_answer_sentence_on_a_relay_rail_overheat_run_names_both_readings(
        tmp_path: Path) -> None:
    """CTO blocker: the 4-instrument bench (psu/dmm/relay/temp) must
    render a full sentence, including the temperature reading and the
    regulator's own limit, not just the rail."""
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(RELAY_RAIL_TASK)).card
    seed = next(s for s in range(500)
               if fault_mod.realized_fault(card, s).fault_id == "overheat")
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=tmp_path)
    answer(run_id, "overheat", state_dir=tmp_path)

    payload = run_payload(run_id, state_dir=tmp_path)
    sentence = payload["answer_sentence"]
    assert "rail reads" in sentence
    assert "regulator" in sentence
    assert "above its" in sentence
    assert sentence.endswith("Correct.")


def test_export_of_a_relay_rail_run_builds_and_names_all_four_addresses(tmp_path: Path) -> None:
    """CTO blocker 5: the morning demo is the relay-rail card -- the export
    must build end to end for a real 4-instrument run, with every
    instrument's address reaching the embedded payload."""
    from shal_arena import fault as fault_mod
    from shal_arena.loader import load_task

    card = load_task(str(RELAY_RAIL_TASK)).card
    seed = next(s for s in range(500)
               if fault_mod.realized_fault(card, s).fault_id == "overheat")
    run_id = start_run(str(RELAY_RAIL_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=tmp_path)
    answer(run_id, "overheat", state_dir=tmp_path)

    html = build_export(run_id, state_dir=tmp_path)
    for addr in ("psu0", "dmm0", "relay0", "temp0"):
        assert f'"{addr}"' in html
    assert "power_on" in html


def test_export_from_a_capture_with_a_task_path_from_another_machine(tmp_path: Path) -> None:
    """CTO blocker: a capture made elsewhere carries an absolute
    `task_path` that does not exist on this machine -- the payload must
    still build, falling back to the packaged task of the same name."""
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, "ok", state_dir=tmp_path)

    state_path = tmp_path / f"{run_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["task_path"] = "/nonexistent/on/this/machine/rail-3v3.yaml"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    payload = run_payload(run_id, state_dir=tmp_path)
    assert payload["task_id"] == "rail-3v3"
