"""issue #498: the Play UI measures at a named point. On `power-28v` one DMM
(dmm0) probes TP1-TP3, so its measure control offers those points and the
measure call sends the chosen one as `at` -- the CLI's own `--at`, through
the same `take_measurement`. `at` is required there, refused on a one-point
instrument, and an unknown point is refused with the valid ones listed."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shal import registry

from shal_arena import fault as fault_mod
from shal_arena.loader import load_task
from shal_arena.ui.page import render_play_page
from shal_arena.ui.server import HOST, serve

_PKG = Path(__file__).resolve().parents[1] / "src" / "shal_arena"
_LOADED = load_task(_PKG / "tasks" / "power-28v.yaml")
_RAILS = {r.test_point: r for r in _LOADED.card.rails}
_POINTS = ["tp_12v", "tp_5v", "tp_3v3"]

#: the same clean-slot fixture test_ui_play.py uses, for the same reason.
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


def _post(httpd, path: str, body: dict) -> tuple[int, dict]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _get(httpd, path: str) -> tuple[int, dict]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


@pytest.fixture
def play_server(tmp_path: Path):
    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _ok_seed() -> int:
    return next(s for s in range(200)
                if fault_mod.realized_fault(_LOADED.card, s).fault_id == "ok")


def _powered_28v(httpd) -> str:
    """Start power-28v through Play, close relay0 and drive 28.0 V -- the
    question's own setup -- all with the Play buttons' routes."""
    status, started = _post(httpd, "/api/play/start", {"task": "power-28v", "seed": _ok_seed()})
    assert status == 200, started
    status, doc = _post(httpd, "/api/play/switch", {"address": "relay0", "on": True})
    assert status == 200, doc
    status, doc = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 28.0})
    assert status == 200 and doc["sent"], doc
    return started["run_id"]


def test_the_dmm_control_offers_the_tasks_own_points(play_server) -> None:
    run_id = _powered_28v(play_server)
    status, payload = _get(play_server, f"/api/run/{run_id}")
    assert status == 200
    controls = {c["address"]: c for c in payload["controls"]}
    assert controls["dmm0"]["kind"] == "measure"
    assert controls["dmm0"]["points"] == _POINTS
    # a one-point instrument has no point list: nothing to choose
    assert "points" not in controls["temp0"]
    assert "points" not in controls["psu0"]


@pytest.mark.parametrize("point", _POINTS)
def test_measure_sends_the_chosen_point(play_server, point: str) -> None:
    _powered_28v(play_server)
    status, doc = _post(play_server, "/api/play/measure", {"address": "dmm0", "at": point})
    assert status == 200, doc
    assert doc["point"] == point
    rail = _RAILS[point]
    lo, hi = rail.nominal_v * (1 - rail.tol_pct / 100), rail.nominal_v * (1 + rail.tol_pct / 100)
    assert lo <= doc["reading"] <= hi, (point, doc["reading"])


def test_a_measure_with_at_turns_the_answer_grid_on(play_server) -> None:
    run_id = _powered_28v(play_server)
    assert _get(play_server, f"/api/run/{run_id}")[1]["has_measurement"] is False
    status, _ = _post(play_server, "/api/play/measure", {"address": "dmm0", "at": "tp_5v"})
    assert status == 200
    assert _get(play_server, f"/api/run/{run_id}")[1]["has_measurement"] is True


def test_an_unknown_point_shows_the_valid_list(play_server) -> None:
    _powered_28v(play_server)
    status, doc = _post(play_server, "/api/play/measure", {"address": "dmm0", "at": "tp_9v"})
    assert status == 400
    error = doc["error"]
    assert "tp_9v" in error["message"]
    for point in _POINTS:
        assert point in error["message"]
        assert point in error["fix"]


def test_no_point_on_a_several_point_instrument_is_refused(play_server) -> None:
    _powered_28v(play_server)
    status, doc = _post(play_server, "/api/play/measure", {"address": "dmm0"})
    assert status == 400
    assert all(point in doc["error"]["fix"] for point in _POINTS)


def test_a_point_on_a_one_point_instrument_is_refused(play_server) -> None:
    _powered_28v(play_server)
    status, doc = _post(play_server, "/api/play/measure",
                        {"address": "temp0", "at": "tp_u2_temp"})
    assert status == 400
    assert "only for an instrument with several probe points" in doc["error"]["message"]


def test_a_one_point_task_still_measures_without_at(play_server) -> None:
    """Additive only: rail-3v3's one-point dmm0 measures as before."""
    status, _ = _post(play_server, "/api/play/start", {"task": "rail-3v3", "seed": 1})
    assert status == 200
    status, doc = _post(play_server, "/api/play/drive", {"address": "psu0", "volts": 5.0})
    assert status == 200, doc
    status, doc = _post(play_server, "/api/play/measure", {"address": "dmm0"})
    assert status == 200, doc
    assert doc["address"] == "dmm0"


@pytest.mark.parametrize("at", [5, "", ["tp_5v"], True])
def test_a_non_string_or_empty_at_is_400(play_server, at) -> None:
    _powered_28v(play_server)
    status, doc = _post(play_server, "/api/play/measure", {"address": "dmm0", "at": at})
    assert status == 400
    assert "'at' must be a non-empty string" in doc["error"]["message"]


def test_the_page_sends_the_chosen_point_from_a_picker() -> None:
    """The browser half: the measure control with `points` renders a point
    picker, and its button posts `{address, at}` with the picked value."""
    html = render_play_page([], None)
    assert 'el("select")' in html
    assert "c.points.forEach" in html
    assert "measure(c.address, pick.value)" in html
    assert 'act("/api/play/measure", at ? {address, at} : {address}' in html


def test_a_later_successful_start_clears_the_picker_flash() -> None:
    """issue #498 (6): a refused start's message must not stay up after a
    later start succeeds."""
    html = render_play_page([], None)
    start = html[html.index("function startTask(name)"):html.index("function flashInto(")]
    ok_path = start[start.index("if (status !== 200)"):]
    ok_path = ok_path[ok_path.index("return;") + len("return;"):]
    assert '$("picker-flash").hidden = true;' in ok_path
