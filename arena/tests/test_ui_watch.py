"""issue #406 Done-when: the server starts on `--port 0`, follows a real run
in progress (a real runner sample, not a hand-built fixture), and replays a
finished one -- the same server, before and after `answer` closes it."""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.server import HOST, serve

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _get_json(httpd, path: str) -> dict:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return json.loads(resp.read())


def test_watch_follows_a_run_in_progress_then_replays_it_finished(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]

    httpd = serve(run_id, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        # in progress: open, no record/score, timeline grows as real calls land
        before = _get_json(httpd, f"/api/run/{run_id}")
        assert before["closed"] is False
        assert before["record"] is None and before["score"] is None
        assert before["timeline"] == []

        drive_input(run_id, "psu0", 30.0, state_dir=tmp_path)   # refused
        drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)    # applied
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)

        mid = _get_json(httpd, f"/api/run/{run_id}")
        assert mid["closed"] is False
        assert len(mid["timeline"]) >= 2
        assert any(e["kind"] == "refused" for e in mid["timeline"])
        assert mid["card"]["applied"].get("vin") == 5.0

        # finished: the same server, the same endpoint, now a replay
        answer(run_id, "ok", state_dir=tmp_path)
        after = _get_json(httpd, f"/api/run/{run_id}")
        assert after["closed"] is True
        assert after["record"]["given"] == "ok"
        assert after["score"] is not None
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_port_0_picks_a_free_port(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    httpd = serve(run_id, state_dir=tmp_path, port=0, open_browser=False)
    try:
        assert httpd.server_address[1] != 0
    finally:
        httpd.server_close()
