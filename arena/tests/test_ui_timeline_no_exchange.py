"""issue #474 (a regression from #459): bus exchange rows (`kind:
"exchange"`, and SCPI's own `query`/`write` rows that carry a `cmd`) landed
in the rendered Timeline as if they were agent steps -- raw protocol text
("fc":5,... -> {...}"), "Switched relay0 on" shown twice (once for the
agent's own `call`, once for the bus-layer `exchange` it made), and the
step count inflated. They stay in `payload.timeline` (real data, for
#447's own log table) -- this is a RENDERING fix only, in `page.py`'s own
JS, never in `payload.timeline` or the run log.

No browser here: a small Node DOM stub (gated on `node` being on PATH,
same pattern `test_card_page.py` uses) runs the page's real, shipped
`_SCRIPT` against a real run's real payload."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shal_arena.runner import call_op, drive_input, start_run, take_measurement
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import _SCRIPT, render_watch_page

from .conftest import (
    PASSING_DMM_DRIVER,
    PASSING_RELAY_DRIVER,
    PASSING_TEMP_DRIVER,
    RELAY_RAIL_TASK,
)
from .test_exchange_log import _play_all_three_protocols

_DOM_STUB = """
function makeEl() {
  const el = {
    _text: "", _html: "",
    classList: { add() {}, remove() {} },
    style: {},
    children: [],
    appendChild(c) { el.children.push(c); },
    setAttribute() {}, getAttribute() { return null; },
  };
  Object.defineProperty(el, "textContent", {
    get() { return el._text; },
    set(v) { el._text = String(v); el._html = String(v); },
  });
  Object.defineProperty(el, "innerHTML", {
    get() { return el._html; },
    set(v) { el._html = String(v); },
  });
  return el;
}
const _elements = {};
global.document = {
  getElementById(id) { if (!_elements[id]) _elements[id] = makeEl(); return _elements[id]; },
  createElement() { return makeEl(); },
  createTextNode(text) { return { textContent: String(text) }; },
  querySelector() { return null; },
};
"""


def _render_with_node(payload: dict) -> dict:
    """Runs the page's real `_SCRIPT` (never a reimplementation) against a
    minimal DOM stub, then returns every stubbed element's own
    textContent/innerHTML by id -- enough to check what a browser would
    actually show, with no browser."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (
        _DOM_STUB + _SCRIPT
        + "\nconst payload = " + json.dumps(payload) + ";"
        + '\nrender(Object.assign({}, payload, {mode: payload.closed ? "replay" : "live"}));'
        + "\nconsole.log(JSON.stringify(Object.fromEntries("
        + "Object.entries(_elements).map(([k, v]) => [k, {text: v.textContent, "
        + "html: v.innerHTML}]))));"
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "render.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _relay_rail_run_with_exchange_log(tmp_path: Path, state_dir: Path) -> str:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=state_dir)["run_id"]
    drive_input(run_id, "psu0", 12.0, state_dir=state_dir)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=state_dir)
    call_op(run_id, "relay0", PASSING_RELAY_DRIVER, "set_relay", ["0", "true"],
           state_dir=state_dir)
    take_measurement(run_id, "temp0", PASSING_TEMP_DRIVER, state_dir=state_dir)
    return run_id


def test_exchange_rows_are_still_in_the_page_data(tmp_path: Path) -> None:
    run_id = _play_all_three_protocols(tmp_path)
    payload = run_payload(run_id, state_dir=tmp_path)
    kinds = {e["kind"] for e in payload["timeline"]}
    assert "exchange" in kinds  # #447's own future log table still has this to read


def test_rendered_step_count_is_the_same_with_the_exchange_log_on_or_off(tmp_path: Path) -> None:
    on_dir = tmp_path / "on"
    run_id_on = _relay_rail_run_with_exchange_log(tmp_path, on_dir)
    payload_on = run_payload(run_id_on, state_dir=on_dir)
    elements_on = _render_with_node(payload_on)

    # the "off" run: the exact timeline shape a run would have had before
    # #459 (every `kind: "exchange"` row stripped -- SCPI's own
    # `query`/`write` rows predate #459 and are untouched by this fix,
    # already paired away by `hasPairedReading` when they have a matching
    # `reading`).
    payload_off = dict(payload_on)
    payload_off["timeline"] = [e for e in payload_on["timeline"] if e["kind"] != "exchange"]
    elements_off = _render_with_node(payload_off)

    assert elements_on["verdict-bar"]["html"] == elements_off["verdict-bar"]["html"]
    assert elements_on["timeline-list"]["html"] == elements_off["timeline-list"]["html"]


def test_no_timeline_row_holds_raw_protocol_text(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    timeline_html = elements["timeline-list"]["html"]
    assert '"fc"' not in timeline_html
    assert not re.search(r"[0-9a-f]{4,}\"?\s*->\s*\"?[0-9a-f]{4,}", timeline_html)


def test_switched_relay_on_appears_once(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    assert elements["timeline-list"]["html"].count("Switched relay0 on") == 1


def test_question_text_appears_under_the_title_live_and_in_export(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)

    live = render_watch_page(run_id, payload)
    assert payload["question"] in live

    from shal_arena.runner import answer
    answer(run_id, "ok", state_dir=state_dir)
    export = build_export(run_id, state_dir=state_dir)
    assert payload["question"] in export


def test_drive_row_still_reads_exactly_through_the_shal_gate(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    assert "drive 12.00 V, through the SHAL gate" in elements["timeline-list"]["html"]
