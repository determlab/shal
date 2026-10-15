"""issue #489: the evidence page's window listing the agent's `shal-arena`
commands, read from `<run>.cli.jsonl` (#460). The rows are built in the
payload (`run_payload`'s `cli_log`), embedded as data in the live page and
in the export alike, and set by the page script with `textContent` only."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from shal_arena.runner import start_run
from shal_arena.store import RunStore
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import _SCRIPT, render_watch_page

from .conftest import SAMPLE_TASK
from .test_ui_export import _finished_run

TITLE = "The agent's shal-arena commands, as it ran them"
EMPTY = "No shal-arena commands were recorded for this run."
HOSTILE = "</script><img src=x onerror=alert(1)>"


def _line(argv: list[str], code: int = 0, json_: Any = None, text: str | None = None) -> str:
    return json.dumps({"time": "2026-10-15T00:00:00Z", "argv": argv, "exit_code": code,
                       "json": json_, "text": text})


def _write_log(tmp_path: Path, run_id: str, lines: list[str]) -> None:
    RunStore(tmp_path).cli_log_path(run_id).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _embedded(html: str) -> dict[str, Any]:
    m = re.search(r'<script id="run-data" type="application/json">(.*?)</script>', html, re.S)
    assert m is not None
    return json.loads(m.group(1))


def _three(tmp_path: Path, run_id: str) -> None:
    _write_log(tmp_path, run_id, [
        _line(["start", "task.yaml"], 0, {"run_id": run_id, "ok": True}),
        _line(["measure", run_id, "dmm0"], 1, {"ok": False, "error": {"type": "boom"}}),
        _line(["answer", run_id, "ok"], 0, None, "answered ok\n"),
    ])


def test_three_lines_make_three_rows_in_live_page_and_export(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    _three(tmp_path, run_id)
    live = render_watch_page(run_id, run_payload(run_id, state_dir=tmp_path))
    export = build_export(run_id, state_dir=tmp_path)
    for html in (live, export):
        assert 'id="cli-window"' in html
        assert TITLE in html   # the script sets it as the window's header text
        rows = _embedded(html)["cli_log"]["rows"]
        assert [r["command"] for r in rows] == [
            "shal-arena start task.yaml", f"shal-arena measure {run_id} dmm0",
            f"shal-arena answer {run_id} ok"]
        assert [r["exit_code"] for r in rows] == [0, 1, 0]
        assert rows[0]["answer"] == json.dumps({"run_id": run_id, "ok": True})
        assert rows[2]["answer"] == "answered ok"


def test_a_non_zero_exit_code_is_marked_failed(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    _three(tmp_path, run_id)
    rows = run_payload(run_id, state_dir=tmp_path)["cli_log"]["rows"]
    assert [r["failed"] for r in rows] == [False, True, False]
    assert 'data-state", "failed"' in _SCRIPT


def test_a_long_answer_is_cut_with_an_ellipsis_and_null_json_shows_text(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    _write_log(tmp_path, run_id, [
        _line(["a"], 0, {"k": "x" * 300}),
        _line(["b"], 0, None, "y" * 300),
        _line(["c"], 0, None, "plain text"),
    ])
    rows = run_payload(run_id, state_dir=tmp_path)["cli_log"]["rows"]
    assert rows[0]["answer"] == json.dumps({"k": "x" * 300})[:120] + "…"
    assert rows[1]["answer"] == "y" * 120 + "…"
    assert rows[2]["answer"] == "plain text"


def test_replay_label_is_in_the_window_header_only_for_a_replay() -> None:
    # the header is built by the page script from the payload's mode, the
    # same switch the top label row uses: LIVE while open, else the replay label
    body = _SCRIPT.split("function renderCliWindow", 1)[1].split("\nfunction ", 1)[0]
    assert 'payload.mode === "live"' in body
    assert 'if (!live) head.appendChild(el("span", "labels", "Replay of a recorded run"))' in body


def test_no_cli_log_shows_the_empty_line_and_the_page_still_renders(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    assert not RunStore(tmp_path).cli_log_path(run_id).exists()
    payload = run_payload(run_id, state_dir=tmp_path)
    assert payload["cli_log"] == {"rows": [], "skipped": 0}
    assert EMPTY in _SCRIPT
    assert TITLE in render_watch_page(run_id, payload)
    assert TITLE in build_export(run_id, state_dir=tmp_path)
    _write_log(tmp_path, run_id, [])   # an empty file is the same
    assert run_payload(run_id, state_dir=tmp_path)["cli_log"] == {"rows": [], "skipped": 0}


def test_a_broken_line_is_skipped_and_counted(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    _write_log(tmp_path, run_id, [_line(["a"]), "{not json", _line(["b"], 2)])
    log = run_payload(run_id, state_dir=tmp_path)["cli_log"]
    assert [r["command"] for r in log["rows"]] == ["shal-arena a", "shal-arena b"]
    assert log["skipped"] == 1
    assert "could not be read" in _SCRIPT


def test_a_hostile_argv_stays_inert(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    _write_log(tmp_path, run_id, [_line(["answer", run_id, HOSTILE], 0, {"v": HOSTILE})])
    live = render_watch_page(run_id, run_payload(run_id, state_dir=tmp_path))
    export = build_export(run_id, state_dir=tmp_path)
    for html in (live, export):
        assert HOSTILE not in html
        assert "\\u003c/script>\\u003cimg src=x onerror=alert(1)>" in html
        assert html.count("</script>") == 2   # the data blob and the page script, no third
        assert HOSTILE in _embedded(html)["cli_log"]["rows"][0]["command"]
    # the window sets every value with textContent: no innerHTML on its path
    body = _SCRIPT.split("function renderCliWindow", 1)[1].split("\nfunction ", 1)[0]
    assert "innerHTML" not in body


def test_a_run_with_no_commands_yet_has_an_empty_window(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    assert run_payload(run_id, state_dir=tmp_path)["cli_log"]["rows"] == []
