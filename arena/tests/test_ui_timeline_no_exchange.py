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

import html
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shal_arena.runner import answer as runner_answer
from shal_arena.runner import call_op, drive_input, start_run, take_measurement
from shal_arena.ui.data import run_payload
from shal_arena.ui.page import _SCRIPT, _shell

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


def _render_single_row_html(entry: dict) -> str:
    """`stepRowHtml` builds a plain HTML string -- no DOM touched -- so this
    needs no stub at all, just the real `_SCRIPT` and one call, bypassing
    `visibleSteps`'s own filtering entirely (used only to prove a raw,
    UNFILTERED exchange row really does contain the raw protocol text the
    main test below checks is absent from the real, filtered render)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = _SCRIPT + "\nconsole.log(stepRowHtml(" + json.dumps(entry) + ", 0));"
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "row.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


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
    """CTO review on #475 round 1, must-fix 1: `escapeHtml` turns `"` into
    `&quot;`, so the raw substrings below never appear literally in the
    rendered `innerHTML` whether or not the bug is fixed -- `html.unescape`
    first, so the check is a check of the TEXT, not of HTML's own quoting."""
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    timeline_html = html.unescape(elements["timeline-list"]["html"])
    assert '"fc"' not in timeline_html
    assert not re.search(r"[0-9a-f]{4,}\"?\s*->\s*\"?[0-9a-f]{4,}", timeline_html)

    # proves the 2 checks above can fail: an exchange row rendered
    # directly, bypassing `visibleSteps`'s own filtering (the pre-#474
    # shape), really does carry this raw protocol text once unescaped --
    # confirming the checks above are not vacuous.
    exchange_entry = next(e for e in payload["timeline"] if e["kind"] == "exchange"
                          and e.get("detail", {}).get("bus_family") == "sim_msg")
    unfiltered_html = html.unescape(_render_single_row_html(exchange_entry))
    assert '"fc"' in unfiltered_html


def test_switched_relay_on_appears_once(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    assert elements["timeline-list"]["html"].count("Switched relay0 on") == 1


def test_question_text_appears_under_the_title_live_and_in_export(tmp_path: Path) -> None:
    """CTO review on #475 round 1, must-fix 2: both pages embed the full
    `payload` as JSON data for `_SCRIPT`'s own `render()` to read -- the
    question text is IN the HTML either way, shown or not. Rendering
    through `_render_with_node` (as the "no raw protocol text" test above
    already does) checks what the stubbed `#question` element's own
    `textContent` actually ends up holding, live and for the closed
    (export) payload alike; `_shell`'s own template order is checked
    separately, statically, with no node needed."""
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)

    elements_live = _render_with_node(payload)
    assert elements_live["question"]["text"] == payload["question"]

    runner_answer(run_id, "ok", state_dir=state_dir)
    export_payload = run_payload(run_id, state_dir=state_dir)
    elements_export = _render_with_node(export_payload)
    assert elements_export["question"]["text"] == export_payload["question"]

    shell_html = _shell(run_id)
    assert shell_html.index('id="title"') < shell_html.index('id="question"')


def test_drive_row_still_reads_exactly_through_the_shal_gate(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    elements = _render_with_node(payload)

    assert ("Powered the card at 12.00 V, through the SHAL gate"
            in elements["timeline-list"]["html"])
