"""issue #407: PLAY routes give the same record/score as the same calls
through the CLI runner (AGENTS.md: no second game logic) -- `shal-arena ui`
with no `--run` starts with no run open; `POST /api/play/start` opens one,
then `measure`/`drive`/`switch`/`answer` drive it exactly the way
`take_measurement`/`drive_input`/`call_op`/`answer` do, because they ARE
those functions."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from shal_arena.runner import answer as runner_answer
from shal_arena.runner import drive_input, start_run, take_measurement
from shal_arena.ui.server import HOST, serve

from .conftest import PASSING_DMM_DRIVER, RELAY_RAIL_TASK, SAMPLE_TASK


def _post(httpd, path: str, body: dict) -> tuple[int, dict]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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


def test_start_lists_tasks_before_any_run_is_open(play_server):
    httpd, _state_dir = play_server
    status, doc = _get(httpd, "/")
    assert status == 200
    assert doc["ok"] is True
    names = {t["name"] for t in doc["tasks"]}
    assert "rail-3v3" in names


def test_start_drive_measure_answer_match_the_cli_runner(play_server):
    httpd, state_dir = play_server

    status, started = _post(httpd, "/api/play/start", {"task": str(SAMPLE_TASK), "seed": 1})
    assert status == 200, started

    # the SAME seed, same task, through the CLI runner directly -- the
    # separate state dir keeps the two runs from colliding
    cli_state_dir = state_dir / "cli"
    cli_run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=cli_state_dir)["run_id"]

    status, driven = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 5.0})
    assert status == 200, driven
    cli_driven = drive_input(cli_run_id, "psu0", 5.0, state_dir=cli_state_dir)
    assert driven["sent"] == cli_driven["sent"]
    assert driven["side_effect"] == cli_driven["side_effect"]

    status, measured = _post(httpd, "/api/play/measure", {"address": "dmm0"})
    assert status == 200, measured
    cli_measured = take_measurement(cli_run_id, "dmm0", PASSING_DMM_DRIVER,
                                    state_dir=cli_state_dir)
    assert measured["reading"] == cli_measured["reading"]

    status, answered = _post(httpd, "/api/play/answer", {"value": "ok"})
    assert status == 200, answered
    cli_answered = runner_answer(cli_run_id, "ok", state_dir=cli_state_dir)
    assert answered["correct"] == cli_answered["correct"]
    assert answered["disqualified"] == cli_answered["disqualified"]
    assert answered["score"]["false_fails"] == cli_answered["score"]["false_fails"]


def test_relay_rail_switch_control_through_play(play_server):
    """CTO review on #407's mock, condition 1: relay-rail's own instruments
    (not psu0/dmm0) -- the relay's own 'switch' control (`call set_relay`),
    plus temp0's 'measure' control (`take_measurement`, same function as
    the DMM, different instrument)."""
    httpd, state_dir = play_server
    status, started = _post(httpd, "/api/play/start",
                            {"task": str(RELAY_RAIL_TASK), "seed": 1})
    assert status == 200, started
    run_id = started["run_id"]

    # the run payload's own controls (via GET /api/run/<id>) name a switch
    # control for relay0 and a measure control for temp0 -- never hardcoded
    status, payload = _get(httpd, f"/api/run/{run_id}")
    assert status == 200
    controls = {c["address"]: c for c in payload["controls"]}
    assert controls["relay0"]["kind"] == "switch"
    assert controls["temp0"]["kind"] == "measure"
    assert controls["psu0"]["kind"] == "drive"
    assert controls["dmm0"]["kind"] == "measure"

    status, switched = _post(httpd, "/api/play/switch", {"address": "relay0", "on": False})
    assert status == 200, switched
    assert switched["side_effect"] == "write"

    status, temp = _post(httpd, "/api/play/measure", {"address": "temp0"})
    assert status == 200, temp
    assert "reading" in temp
