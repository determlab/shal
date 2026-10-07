"""issue #407: the Play routes enforce the same rules the CLI already does
-- an answer before any measurement is refused with the runner's own
reason (never a second rule); a gate-refused drive shows "nothing was
sent", the same way `shal-arena drive` already does."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from shal_arena.ui.server import HOST, serve

from .conftest import RELAY_RAIL_TASK, SAMPLE_TASK


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


def test_answer_before_any_measurement_is_refused_and_the_run_stays_open(play_server):
    httpd = play_server
    status, started = _post(httpd, "/api/play/start", {"task": str(SAMPLE_TASK), "seed": 1})
    assert status == 200, started

    status, refused = _post(httpd, "/api/play/answer", {"value": "ok"})
    assert status == 400
    assert refused["ok"] is False
    assert "measur" in refused["error"]["message"]
    assert refused["error"]["fix"]

    # the run is still open -- a refused answer never closed it (unlike a
    # real `answer`, which always closes, disqualified or not)
    status, driven = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 5.0})
    assert status == 200, driven


def test_a_gate_refused_drive_shows_nothing_was_sent(play_server):
    httpd = play_server
    status, started = _post(httpd, "/api/play/start", {"task": str(SAMPLE_TASK), "seed": 1})
    assert status == 200, started

    # 30 V on rail-3v3's vin is well past the card's documented abs max --
    # shal's own damage gate refuses it, the same way `shal-arena drive`'s
    # own CLI path does
    status, refused = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 30.0})
    assert status == 200, refused
    assert refused["sent"] is False
    assert "nothing was sent" in refused["reason"]


def test_switch_on_a_non_power_switch_instrument_is_the_runners_own_refusal(play_server):
    """Condition 1's own guardrail, from the other direction: Play routes a
    `switch` call only to a power-switch case; asking it to drive a plain
    instrument (psu0) must fail with the SAME refusal `call_op` already
    gives a player who tried `set_relay` on the wrong address (AGENTS.md:
    no second game logic -- this is that one check, not a new one)."""
    httpd = play_server
    status, started = _post(httpd, "/api/play/start", {"task": str(RELAY_RAIL_TASK), "seed": 1})
    assert status == 200, started

    status, refused = _post(httpd, "/api/play/switch", {"address": "psu0", "on": True})
    assert status == 400
    assert refused["ok"] is False
