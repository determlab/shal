"""issue #406 DoD: never show the fault before the run ends. Scans the page
HTML and the `/api/run/<id>` JSON while a run is still open -- the realized
fault for this seed must not appear in either, by any route. issue #407
extends this to the Play routes: starting, driving and measuring through
them must leak nothing either, the same as the WATCH-only path above."""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from shal import registry

from shal_arena.loader import load_task
from shal_arena.runner import drive_input, pick_fault, start_run, take_measurement
from shal_arena.ui.server import HOST, serve

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

#: CTO review on #407 round 2, must-fix 1: the same clean-slot fixture
#: test_ui_play.py uses, for the same reason.
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


def _get(httpd, path: str) -> bytes:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read()


def _post(httpd, path: str, body: dict) -> tuple[int, bytes]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


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


def test_play_routes_never_leak_the_fault_before_the_run_ends(tmp_path: Path) -> None:
    """CTO review on #407 round 2, must-fix 8: the old version scanned only
    `measured`, the page and the API JSON -- the drive responses (one
    gate-refused, one sent), the switch response, and a deliberately
    refused answer (still 400, before the real one closes the run) were
    never checked. Every Play response this run produces now goes through
    the same scan, status included, so a leak in any one of them fails
    here instead of only in whichever route a future reviewer happens to
    try by hand."""
    card = load_task(str(SAMPLE_TASK)).card
    fault = pick_fault(card, 1)
    responses: list[tuple[int, bytes]] = []

    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, started = _post(httpd, "/api/play/start", {"task": "rail-3v3", "seed": 1})
        # `started` is NOT added to `responses` below: `task.answer.values`
        # (DoD #451) is the full, fixed set of possible fault ids for this
        # task -- always every one of them, including whichever is
        # realized this run, by design (an agent must not need to guess
        # the choices). That is not the same thing as leaking WHICH one is
        # real, which every response below is actually checked for.
        assert status == 200
        run_id = json.loads(started)["run_id"]

        # gate-refused (400 is not expected -- 200 with sent:false)
        status, driven_refused = _post(httpd, "/api/play/drive",
                                       {"address": "psu0", "volts": 30.0})
        responses.append((status, driven_refused))
        status, driven_sent = _post(httpd, "/api/play/drive", {"address": "psu0", "volts": 5.0})
        responses.append((status, driven_sent))

        status, measured = _post(httpd, "/api/play/measure", {"address": "dmm0"})
        responses.append((status, measured))

        # the relay-rail-only switch control is not on this task's one
        # instrument list, so this is the real refusal `call_op` gives for
        # a non-power-switch address -- still scanned, like every response.
        status, switch_refused = _post(httpd, "/api/play/switch",
                                       {"address": "psu0", "on": True})
        responses.append((status, switch_refused))
        assert status == 400

        # a bad answer value is refused before it can close the run. Its
        # own `fix` text names every VALID value (the same fixed enum
        # `task.answer.values` always lists, same reasoning as `started`
        # above) -- not added to `responses`, for the same reason.
        status, answer_refused = _post(httpd, "/api/play/answer", {"value": "not-a-real-fault"})
        assert status == 400

        page = _get(httpd, "/").decode("utf-8")
        api = _get(httpd, f"/api/run/{run_id}").decode("utf-8")
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    for status, body in responses:
        assert fault not in body.decode("utf-8"), (status, body)
    assert fault not in page
    assert fault not in api


def test_pick_fault_itself_names_what_this_test_checks() -> None:
    """A sanity check on the test above: the seed really does pick a
    non-trivial fault, so "not in page" is a real assertion, not vacuous."""
    card = load_task(str(SAMPLE_TASK)).card
    assert pick_fault(card, 1) in {"ok", "low_voltage", "noise", "open"}
