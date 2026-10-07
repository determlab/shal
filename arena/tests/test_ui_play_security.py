"""CTO review on #407 round 2, must-fix 3, 5, 6, 7: Play's own HTTP routes
are not a trusted local caller like the CLI -- a body comes off the
network, from whatever a browser (or an attacker's page) POSTs.

- must-fix 3: a strict body schema per route. Every failure is 400 in the
  shared `{ok, error: {type, message, fix}}` shape, with a non-empty
  `fix`, and never a dropped connection or a value silently sent as 0/NaN.
- must-fix 5a: a cross-origin request (wrong Content-Type, Host or
  Origin) never reaches a route -- 403.
- must-fix 5b: `/api/play/start` only ever takes a packaged task NAME,
  never a file path (see also test_ui_play_security.py's own body-schema
  cases below).
- must-fix 5c: nothing sent over HTTP carries a real filesystem path.
- must-fix 6: every POST route 405s in WATCH mode (a server started with
  a fixed `--run <id>`).
- must-fix 7: `/api/play/start` works again after `/api/play/answer`
  closes the run on the same server.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shal import registry

from shal_arena.runner import start_run
from shal_arena.ui.server import HOST, serve

from .conftest import SAMPLE_TASK

_PLAY_COMPATIBLES = ("arena,bench-psu1", "arena,bench-dmm1",
                    "arena,bench-relay1", "arena,bench-temp1")


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


def _post_raw(httpd, path: str, data: bytes, headers: dict) -> tuple[int, bytes]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(httpd, path: str, body, headers: dict | None = None) -> tuple[int, dict]:
    data = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    status, raw = _post_raw(httpd, path, data, hdrs)
    return status, json.loads(raw)


def _get(httpd, path: str) -> tuple[int, dict]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def play_server(tmp_path: Path):
    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd, tmp_path
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _started_run(httpd, task: str = "rail-3v3") -> str:
    status, started = _post(httpd, "/api/play/start", {"task": task, "seed": 1})
    assert status == 200, started
    return started["run_id"]


# --------------------------------------------------------------------------- #
# must-fix 3: strict body schema
# --------------------------------------------------------------------------- #

def test_malformed_json_body_is_400_not_a_dropped_connection(play_server):
    httpd, _state_dir = play_server
    status, raw = _post_raw(httpd, "/api/play/start", b"{not json",
                            {"Content-Type": "application/json"})
    assert status == 400
    doc = json.loads(raw)
    assert doc["ok"] is False
    assert doc["error"]["fix"]


def test_a_non_object_json_body_is_400(play_server):
    httpd, _state_dir = play_server
    status, doc = _post(httpd, "/api/play/start", [1, 2, 3])
    assert status == 400
    assert doc["ok"] is False


def test_drive_with_a_string_volts_is_400(play_server):
    httpd, _state_dir = play_server
    _started_run(httpd)
    status, doc = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": "abc"})
    assert status == 400
    assert doc["error"]["fix"]


def test_drive_with_no_volts_is_400_never_sends_zero(play_server):
    httpd, _state_dir = play_server
    _started_run(httpd)
    status, doc = _post(httpd, "/api/play/drive", {"address": "psu0"})
    assert status == 400
    assert doc["error"]["fix"]


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_drive_with_a_non_finite_volts_is_400_never_sent(play_server, token):
    # json.dumps can't encode nan/inf as valid JSON by default -- send the
    # literal token a non-conforming client could still send.
    httpd, _state_dir = play_server
    run_id = _started_run(httpd)
    raw = ('{"address": "psu0", "volts": ' + token + "}").encode("utf-8")
    status, body = _post_raw(httpd, "/api/play/drive", raw, {"Content-Type": "application/json"})
    assert status == 400, body
    doc = json.loads(body)
    assert doc["error"]["fix"]

    status, run_doc = _get(httpd, f"/api/run/{run_id}")
    assert status == 200
    applied = run_doc["card"]["applied"]
    assert all(isinstance(v, (int, float)) and v == v and abs(v) != float("inf")
              for v in applied.values())


def test_switch_with_a_string_on_is_400(play_server):
    httpd, _state_dir = play_server
    _started_run(httpd, task="relay-rail")
    status, doc = _post(httpd, "/api/play/switch", {"address": "relay0", "on": "false"})
    assert status == 400
    assert doc["error"]["fix"]


def test_start_with_no_task_is_400(play_server):
    httpd, _state_dir = play_server
    status, doc = _post(httpd, "/api/play/start", {})
    assert status == 400
    assert doc["error"]["fix"]


def test_start_with_a_non_int_seed_is_400(play_server):
    httpd, _state_dir = play_server
    status, doc = _post(httpd, "/api/play/start", {"task": "rail-3v3", "seed": "1"})
    assert status == 400
    assert doc["error"]["fix"]


# --------------------------------------------------------------------------- #
# must-fix 5b: start takes only a packaged task NAME, never a path
# --------------------------------------------------------------------------- #

def test_start_rejects_a_file_path(play_server, tmp_path):
    httpd, _state_dir = play_server
    sneaky = tmp_path / "not_a_real_file.yaml"
    status, doc = _post(httpd, "/api/play/start", {"task": str(sneaky)})
    assert status == 400
    assert doc["error"]["fix"]


def test_start_rejects_an_absolute_path_even_if_it_exists(play_server):
    httpd, _state_dir = play_server
    status, doc = _post(httpd, "/api/play/start", {"task": str(SAMPLE_TASK)})
    assert status == 400
    assert doc["error"]["fix"]


# --------------------------------------------------------------------------- #
# must-fix 5a: Content-Type / Host / Origin
# --------------------------------------------------------------------------- #

def test_a_text_plain_post_is_rejected(play_server):
    httpd, _state_dir = play_server
    data = json.dumps({"task": "rail-3v3"}).encode("utf-8")
    status, body = _post_raw(httpd, "/api/play/start", data, {"Content-Type": "text/plain"})
    assert status == 403
    assert json.loads(body)["error"]["fix"]


def test_a_mismatched_host_header_is_rejected(play_server):
    httpd, _state_dir = play_server
    data = json.dumps({"task": "rail-3v3"}).encode("utf-8")
    status, body = _post_raw(httpd, "/api/play/start", data,
                             {"Content-Type": "application/json", "Host": "evil.example"})
    assert status == 403
    assert json.loads(body)["error"]["fix"]


def test_a_cross_origin_origin_header_is_rejected(play_server):
    httpd, _state_dir = play_server
    data = json.dumps({"task": "rail-3v3"}).encode("utf-8")
    status, body = _post_raw(httpd, "/api/play/start", data,
                             {"Content-Type": "application/json",
                              "Origin": "http://evil.example"})
    assert status == 403
    assert json.loads(body)["error"]["fix"]


# --------------------------------------------------------------------------- #
# must-fix 5c: no filesystem path in a response
# --------------------------------------------------------------------------- #

def test_task_list_carries_no_filesystem_path(play_server):
    httpd, _state_dir = play_server
    status, doc = _get(httpd, "/")
    assert status == 200
    for task in doc["tasks"]:
        assert "/" not in json.dumps(task) and "\\" not in json.dumps(task)


def test_answer_response_carries_no_filesystem_path(play_server):
    httpd, _state_dir = play_server
    run_id = _started_run(httpd)
    status, measured = _post(httpd, "/api/play/measure", {"address": "dmm0"})
    assert status == 200, measured
    status, answered = _post(httpd, "/api/play/answer", {"value": "ok"})
    assert status == 200, answered
    assert "sim_log" not in answered
    assert run_id  # the run id itself is not a path and stays visible


# --------------------------------------------------------------------------- #
# must-fix 6: WATCH mode never writes
# --------------------------------------------------------------------------- #

def test_every_post_route_405s_in_watch_mode(tmp_path):
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    httpd = serve(run_id, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        for path, body in [
            ("/api/play/start", {"task": "rail-3v3"}),
            ("/api/play/measure", {"address": "dmm0"}),
            ("/api/play/drive", {"address": "psu0", "volts": 1.0}),
            ("/api/play/switch", {"address": "relay0", "on": True}),
            ("/api/play/answer", {"value": "ok"}),
        ]:
            status, doc = _post(httpd, path, body)
            assert status == 405, (path, doc)
            assert doc["error"]["fix"]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


# --------------------------------------------------------------------------- #
# must-fix 7: start works again after answer closes the run
# --------------------------------------------------------------------------- #

def test_start_after_answer_reopens_on_the_same_server(play_server):
    httpd, _state_dir = play_server
    _started_run(httpd)
    status, measured = _post(httpd, "/api/play/measure", {"address": "dmm0"})
    assert status == 200, measured
    status, answered = _post(httpd, "/api/play/answer", {"value": "ok"})
    assert status == 200, answered

    status, restarted = _post(httpd, "/api/play/start", {"task": "rail-3v3", "seed": 2})
    assert status == 200, restarted
    assert restarted["run_id"] != answered.get("run_id")
