"""issue #406: `/api/run/<id>` -- same data the page embeds, server bound to
127.0.0.1 only, and the server can serve no run but the one it was started
to watch (so "only the run's own files are read" holds structurally, not by
convention)."""
from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from shal_arena.runner import drive_input, start_run
from shal_arena.ui.server import HOST, serve

from .conftest import SAMPLE_TASK


@pytest.fixture
def running_server(tmp_path: Path):
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    httpd = serve(run_id, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield run_id, httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(httpd, path: str) -> tuple[int, bytes]:
    url = f"http://{HOST}:{httpd.server_address[1]}{path}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_binds_127_0_0_1_only(running_server):
    _run_id, httpd = running_server
    assert httpd.server_address[0] == HOST == "127.0.0.1"


def test_api_json_equals_what_the_page_embeds(running_server):
    run_id, httpd = running_server
    status, page_body = _get(httpd, "/")
    assert status == 200
    page = page_body.decode("utf-8")

    start = page.index('<script id="run-data" type="application/json">') + len(
        '<script id="run-data" type="application/json">')
    end = page.index("</script>", start)
    embedded = json.loads(page[start:end])

    api_status, api_body = _get(httpd, f"/api/run/{run_id}")
    assert api_status == 200
    api_payload = json.loads(api_body)

    assert embedded == api_payload


def test_the_server_serves_no_run_but_the_one_it_watches(running_server, tmp_path):
    run_id, httpd = running_server
    # a second, real run exists in the same state dir -- the server must
    # never read it, structurally: it was never given that id to watch.
    other_id = start_run(str(SAMPLE_TASK), seed=2, state_dir=tmp_path)["run_id"]
    assert other_id != run_id

    status, body = _get(httpd, f"/api/run/{other_id}")
    assert status == 404
    assert other_id.encode() not in body

    # the watched run's own id still works
    ok_status, _ = _get(httpd, f"/api/run/{run_id}")
    assert ok_status == 200


def test_a_bad_path_is_a_clean_404_not_a_crash(running_server):
    _run_id, httpd = running_server
    status, _ = _get(httpd, "/nothing/here")
    assert status == 404
