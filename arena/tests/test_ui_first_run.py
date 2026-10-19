"""issue #523: three first-run failures in `shal-arena ui`.

1. `localhost:<port>` was refused; only `127.0.0.1:<port>` worked. The server
   now prints every URL it answers on, and each one answers 200.
2. Reopening the page landed on an unfinished run with no way out. The Play
   page has a `Start new run` button; it calls `POST /api/play/new-run`, which
   clears the open run, and the page goes back to the task picker.
3. The result card had no Share. It now has a `Share result` button and the
   line `Sends: task, seed, score, sha256`; the payload is
   `GET /api/share/<run_id>` and carries exactly those four keys.

Each test below is one DoD line of the issue.
"""
from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shal import registry

from shal_arena.store import RunStore
from shal_arena.ui.page import render_play_page, render_watch_page
from shal_arena.ui.server import HOST, serve

#: same clean-slot rule as test_ui_play.py: Play's reference drivers
#: register under these compatibles, so each test starts with none.
_PLAY_COMPATIBLES = ("arena,bench-psu1", "arena,bench-dmm1",
                    "arena,bench-relay1", "arena,bench-temp1")

SHARE_KEYS = {"task", "seed", "score", "sha256"}


@pytest.fixture(autouse=True)
def _clean_registry_slots():
    saved = {c: list(registry._entries.get(c, [])) for c in _PLAY_COMPATIBLES}
    for c in saved:
        registry._entries[c] = []
    try:
        yield
    finally:
        for c, candidates in saved.items():
            registry._entries[c] = candidates


def _request(url: str, *, body: dict | None = None) -> tuple[int, bytes, str]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read(), resp.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def _base(httpd) -> str:
    return f"http://{HOST}:{httpd.server_address[1]}"


def _get(httpd, path: str) -> tuple[int, dict]:
    status, raw, _ = _request(_base(httpd) + path)
    return status, json.loads(raw)


def _post(httpd, path: str, body: dict) -> tuple[int, dict]:
    status, raw, _ = _request(_base(httpd) + path, body=body)
    return status, json.loads(raw)


def _running(httpd):
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return thread


def _stop(httpd, thread) -> None:
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture
def play_server(tmp_path: Path):
    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = _running(httpd)
    try:
        yield httpd, tmp_path
    finally:
        _stop(httpd, thread)


def _closed_run(httpd) -> str:
    status, started = _post(httpd, "/api/play/start", {"task": "rail-3v3", "seed": 7})
    assert status == 200, started
    _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 5.0})
    status, measured = _post(httpd, "/api/play/measure", {"address": "dmm0"})
    assert status == 200, measured
    status, answered = _post(httpd, "/api/play/answer", {"value": "ok"})
    assert status == 200, answered
    return started["run_id"]


# --- DoD 1: localhost and 127.0.0.1 both answer; the printed URL works ------


def test_every_printed_url_answers_200(tmp_path: Path, capsys) -> None:
    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = _running(httpd)
    try:
        printed = capsys.readouterr().out
        urls = re.findall(r"http://[^\s)]+/", printed)
        port = httpd.server_address[1]
        assert f"http://localhost:{port}/" in urls, printed
        assert f"http://127.0.0.1:{port}/" in urls, printed
        for url in urls:
            status, raw, ctype = _request(url)
            assert status == 200, (url, status, raw[:200])
            assert ctype.startswith("text/html"), (url, ctype)
    finally:
        _stop(httpd, thread)


def test_localhost_host_header_is_accepted_on_get_and_post(play_server) -> None:
    """A browser on `http://localhost:<port>/` sends `Host: localhost:<port>`
    and, on a POST, `Origin: http://localhost:<port>`. Both are this machine;
    a foreign Host is still refused (403)."""
    httpd, _ = play_server
    port = httpd.server_address[1]
    for host in (f"localhost:{port}", f"127.0.0.1:{port}"):
        req = urllib.request.Request(_base(httpd) + "/api/play/tasks", headers={"Host": host})
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
    req = urllib.request.Request(
        _base(httpd) + "/api/play/start", method="POST",
        data=json.dumps({"task": "rail-3v3"}).encode("utf-8"),
        headers={"Host": f"localhost:{port}", "Origin": f"http://localhost:{port}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
    bad = urllib.request.Request(_base(httpd) + "/", headers={"Host": f"evil.example:{port}"})
    with pytest.raises(urllib.error.HTTPError) as err:
        urllib.request.urlopen(bad, timeout=5)
    assert err.value.code == 403


# --- DoD 2: an unfinished run shows `Start new run`; it clears the run -----


def test_unfinished_run_page_has_start_new_run_button() -> None:
    page = render_play_page([{"name": "rail-3v3", "level": "easy"}], "some-run")
    assert re.search(r'<button[^>]*id="new-run"[^>]*>\s*Start new run\s*</button>', page), \
        "the Play page must carry a `Start new run` button"
    # the button calls the JSON route and then shows the start screen
    m = re.search(r"function startNewRun\(\)\s*\{(.*?)\n\}", page, re.S)
    assert m, "startNewRun() handler missing"
    assert '"/api/play/new-run"' in m.group(1)
    assert "showPicker()" in m.group(1)
    assert '$("new-run").onclick = startNewRun' in page


def test_start_new_run_clears_the_run_and_shows_the_start_screen(play_server) -> None:
    httpd, _ = play_server
    status, started = _post(httpd, "/api/play/start", {"task": "rail-3v3"})
    assert status == 200
    run_id = started["run_id"]
    # reopening the page: the unfinished run is bound
    _, raw, _ = _request(_base(httpd) + "/")
    assert f'"run_id": "{run_id}"' in raw.decode("utf-8")

    status, doc = _post(httpd, "/api/play/new-run", {})
    assert status == 200, doc
    assert doc == {"ok": True, "cleared": run_id}

    # no run is open any more, and the page is the start screen again
    status, _ = _get(httpd, "/api/play/choices")
    assert status == 400
    _, raw, _ = _request(_base(httpd) + "/")
    assert '"run_id": null' in raw.decode("utf-8")
    # and a fresh run can start at once
    status, again = _post(httpd, "/api/play/start", {"task": "rail-3v3"})
    assert status == 200 and again["run_id"] != run_id


# --- DoD 3: the result card has `Share result` and the Sends line ----------


def test_result_card_has_share_button_and_sends_line(play_server) -> None:
    httpd, _ = play_server
    run_id = _closed_run(httpd)
    _, play_raw, _ = _request(_base(httpd) + "/")
    status, payload = _get(httpd, f"/api/run/{run_id}")
    assert status == 200
    for page in (play_raw.decode("utf-8"), render_watch_page(run_id, payload)):
        assert re.search(r'<button[^>]*id="share"[^>]*>\s*Share result\s*</button>', page)
        assert "Sends: task, seed, score, sha256" in page
        assert "/api/share/" in page


# --- DoD 4: the Share payload has exactly those four fields ---------------


def test_share_payload_has_exactly_four_keys(play_server) -> None:
    httpd, state_dir = play_server
    run_id = _closed_run(httpd)
    status, doc = _get(httpd, f"/api/share/{run_id}")
    assert status == 200, doc
    assert set(doc) == SHARE_KEYS
    score = json.loads(RunStore(state_dir).score_path(run_id).read_text(encoding="utf-8"))
    assert doc["task"] == score["task_id"]
    assert doc["seed"] == score["seed"] == 7
    assert doc["sha256"] == score["record_sha256"]
    # the result card's own headline -- never the hidden fault_type
    assert isinstance(doc["score"], str)
    assert "faults caught" in doc["score"] or "No fault was injected" in doc["score"]
    assert score["fault_type"] not in doc["score"]


def test_share_refuses_an_open_or_unknown_run(play_server) -> None:
    httpd, _ = play_server
    status, started = _post(httpd, "/api/play/start", {"task": "rail-3v3"})
    status, doc = _get(httpd, f"/api/share/{started['run_id']}")
    assert status == 400 and doc["error"]["fix"]
    status, doc = _get(httpd, "/api/share/not-a-run")
    assert status == 404
