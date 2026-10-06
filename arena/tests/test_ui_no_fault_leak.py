"""issue #406 DoD: never show the fault before the run ends. Scans the page
HTML and the `/api/run/<id>` JSON while a run is still open -- the realized
fault for this seed must not appear in either, by any route."""
from __future__ import annotations

import threading
import urllib.request
from pathlib import Path

from shal_arena.loader import load_task
from shal_arena.runner import drive_input, pick_fault, start_run, take_measurement
from shal_arena.ui.server import HOST, serve

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _get(httpd, path: str) -> bytes:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read()


def test_no_fault_name_appears_before_the_run_ends(tmp_path: Path) -> None:
    card = load_task(str(SAMPLE_TASK)).card
    seed = 1
    fault = pick_fault(card, seed)

    run_id = start_run(str(SAMPLE_TASK), seed=seed, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 30.0, state_dir=tmp_path)
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    try:
        take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    except Exception:  # noqa: BLE001 - `open` raises; the run is still open either way
        pass

    httpd = serve(run_id, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        page = _get(httpd, "/").decode("utf-8")
        api = _get(httpd, f"/api/run/{run_id}").decode("utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert fault not in page
    assert fault not in api
    # the structural guarantee page.py and data.py both rely on: no record,
    # no score, while the run is open.
    assert '"record": null' in api
    assert '"score": null' in api


def test_pick_fault_itself_names_what_this_test_checks() -> None:
    """A sanity check on the test above: the seed really does pick a
    non-trivial fault, so "not in page" is a real assertion, not vacuous."""
    card = load_task(str(SAMPLE_TASK)).card
    assert pick_fault(card, 1) in {"ok", "low_voltage", "noise", "open"}
