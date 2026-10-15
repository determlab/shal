"""issue #481: the exported replay ticks once per row the timeline really
shows (never on an `exchange` row, nor on a `measure`/`query` row a later
`reading` folds away), its "N step(s) so far" count matches the rows on
screen at every tick, the closed verdict bar carries the answer sentence
(as text, once on the page), and the power step reads "Powered the card at
12.00 V, through the SHAL gate".

No browser here: the same Node DOM stub `test_ui_timeline_no_exchange.py`
uses runs the shipped `_SCRIPT` plus the export's own `startExport`, with
`setTimeout` replaced by a queue drained in order, and every `render()`
call snapshotted as one frame."""
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
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import _EXPORT_SCRIPT, build_export
from shal_arena.ui.page import _SCRIPT

from .test_ui_timeline_no_exchange import (
    _DOM_STUB,
    _relay_rail_run_with_exchange_log,
    _render_with_node,
)

_STEP_RE = re.compile(r'<div class="step" data-i=')
_COUNT_RE = re.compile(r"(\d+) step\(s\) so far")


def _export_frames(payload: dict) -> list[dict]:
    """Every frame `startExport` renders, in order: each is the stubbed
    elements' own text/html by id, right after that `render()` call."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (
        _DOM_STUB
        + "\nconst _queue = [];"
        + "\nglobal.setTimeout = (fn) => { _queue.push(fn); return _queue.length; };"
        + _SCRIPT + _EXPORT_SCRIPT
        + "\nconst _frames = [];"
        + "\nconst _realRender = render;"
        + "\nrender = function (p) { _realRender(p); _frames.push(Object.fromEntries("
        + "Object.entries(_elements).map(([k, v]) => [k, {text: v.textContent, "
        + "html: v.innerHTML}]))); };"
        + "\nstartExport(" + json.dumps(payload) + ");"
        + "\nlet _guard = 0;"
        + "\nwhile (_queue.length && _guard++ < 10000) _queue.shift()();"
        + "\nconsole.log(JSON.stringify(_frames));"
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "export.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _closed_relay_rail_payload(tmp_path: Path) -> tuple[str, Path, dict]:
    state_dir = tmp_path / "state"
    run_id = _relay_rail_run_with_exchange_log(tmp_path, state_dir)
    runner_answer(run_id, "ok", state_dir=state_dir)
    payload = run_payload(run_id, state_dir=state_dir)
    assert payload["closed"]
    return run_id, state_dir, payload


def _rows(frame: dict) -> int:
    return len(_STEP_RE.findall(frame["timeline-list"]["html"]))


def _page_text(frame: dict) -> str:
    # every element's own rendered markup, unescaped -- what a reader sees
    return "\n".join(html.unescape(v["html"]) for v in frame.values())


def test_export_ticks_once_per_visible_row(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    kinds = [e["kind"] for e in payload["timeline"]]
    assert "exchange" in kinds   # the exchange log is on in this run

    frames = _export_frames(payload)
    # frame 0 is the empty start, the last is the closed page; every frame
    # between is one tick
    ticks = frames[1:-1]
    visible_rows = _rows(frames[-1])
    assert len(ticks) == visible_rows
    assert visible_rows < len(payload["timeline"])
    # each tick adds exactly one row
    assert [_rows(f) for f in ticks] == list(range(1, visible_rows + 1))


def test_every_tick_count_matches_rows_and_never_flashes_attempted(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    timeline = payload["timeline"]
    # non-vacuous: this run has a measure that a later reading completes
    assert any(e["kind"] == "measure" and any(
        o["kind"] == "reading" and o["address"] == e["address"] for o in timeline[i + 1:])
        for i, e in enumerate(timeline))

    frames = _export_frames(payload)
    for frame in frames[:-1]:
        m = _COUNT_RE.search(frame["verdict-bar"]["html"])
        assert m is not None, frame["verdict-bar"]["html"]
        assert int(m.group(1)) == _rows(frame)
        # every measure in this run has a reading, so none ever shows alone
        assert "attempted" not in frame["timeline-list"]["html"]


def test_closed_verdict_bar_holds_the_answer_sentence(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    sentence = payload["answer_sentence"]
    assert sentence

    live = _render_with_node(payload)
    assert sentence in html.unescape(live["verdict-bar"]["html"])
    export_last = _export_frames(payload)[-1]
    assert sentence in html.unescape(export_last["verdict-bar"]["html"])


def test_verdict_bar_shows_a_markup_sentence_as_text(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    payload["answer_sentence"] = "<b>x</b>"
    bar = _render_with_node(payload)["verdict-bar"]["html"]
    assert "&lt;b&gt;x&lt;/b&gt;" in bar
    assert "<b>x</b>" not in bar


def test_verdict_bar_keeps_the_short_reason_with_no_sentence(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    payload["answer_sentence"] = None
    payload["card"] = dict(payload["card"], destroyed=True)
    bar = _render_with_node(payload)["verdict-bar"]["html"]
    assert "card destroyed" in bar


def test_answer_sentence_appears_once_live_and_in_export(tmp_path: Path) -> None:
    run_id, state_dir, payload = _closed_relay_rail_payload(tmp_path)
    sentence = payload["answer_sentence"]

    live = _render_with_node(payload)
    assert _page_text(live).count(sentence) == 1
    assert sentence not in html.unescape(live["result-section"]["html"])
    assert "answer-sentence" not in live["result-section"]["html"]
    # the Result box keeps its other rows
    assert "answered <b>ok</b>" in live["result-section"]["html"]

    export_last = _export_frames(payload)[-1]
    assert _page_text(export_last).count(sentence) == 1
    assert sentence in html.unescape(export_last["verdict-bar"]["html"])

    # the real export file runs this same script on the same payload
    assert "startExport(" in build_export(run_id, state_dir=state_dir)


def test_power_step_reads_powered_the_card(tmp_path: Path) -> None:
    _, _, payload = _closed_relay_rail_payload(tmp_path)
    rows = re.findall(r'<div class="what">([^<]*)</div>',
                      _render_with_node(payload)["timeline-list"]["html"])
    assert "Powered the card at 12.00 V, through the SHAL gate" in [
        html.unescape(r) for r in rows]
