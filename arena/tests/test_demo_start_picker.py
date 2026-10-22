"""issue #520: the opening screen has 5 start cards, and a card starts the
demo at its stage through `shal-arena demo --from STAGE --json` (#508) --
the page keeps no stage data of its own. One test per Done-when line."""
from __future__ import annotations

import contextlib
import io
import json
import re
import threading
import urllib.request
from pathlib import Path

import pytest

from shal_arena import cli, stages
from shal_arena.ui.server import HOST, serve

from .test_ui_play import (
    _clean_registry_slots,  # noqa: F401 - autouse fixture
    _get,
    _post,
)

STAGE_NAMES = ["empty", "picked", "racked", "wired", "ran"]


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


def _cli_first_step(stage: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert cli.main(["demo", "--from", stage, "--json", "--trace"]) == 0
    first = json.loads(out.getvalue())["steps"][0]
    return {k: v for k, v in first.items() if k != "step_ms"}  # timing differs per run


def _page(httpd) -> str:
    with urllib.request.urlopen(f"http://{HOST}:{httpd.server_address[1]}/", timeout=5) as r:
        return r.read().decode("utf-8")


# DoD 1: the opening screen shows the 5 start cards.


def test_opening_screen_shows_the_five_start_cards_racked_by_default(play_server):
    page = _page(play_server)
    assert 'id="demo-cards"' in page
    data = json.loads(re.search(
        r'<script id="play-data" type="application/json">(.*?)</script>', page, re.S).group(1))
    assert data["demo_stages"] == STAGE_NAMES
    assert data["demo_default"] == "racked"
    status, doc = _get(play_server, "/api/demo/stages")
    assert status == 200 and doc["stages"] == STAGE_NAMES and doc["default"] == "racked"


def test_the_cards_are_the_clis_own_stages_not_a_second_copy(play_server):
    _, doc = _get(play_server, "/api/demo/stages")
    assert doc["stages"] == list(stages.STAGES)
    assert doc["default"] == stages.DEFAULT_STAGE


# DoD 2: choosing a card starts the demo at that stage, same first step as the CLI.


@pytest.mark.parametrize("stage", STAGE_NAMES)
def test_choosing_a_card_starts_at_that_stage_like_the_cli(play_server, stage):
    status, doc = _post(play_server, "/api/demo/start", {"stage": stage})
    assert status == 200, doc
    first = {k: v for k, v in doc["steps"][0].items() if k != "step_ms"}
    assert first["step"] == stage and doc["from"] == stage
    assert first == _cli_first_step(stage)


def test_an_unknown_card_is_the_clis_error_naming_the_five_stages(play_server):
    status, doc = _post(play_server, "/api/demo/start", {"stage": "assembled"})
    assert status == 400 and doc["ok"] is False
    assert all(name in doc["error"]["message"] for name in STAGE_NAMES)


# DoD 3: switching between paths keeps the rack state.


def test_switching_cards_keeps_the_rack(play_server):
    _, racked = _post(play_server, "/api/demo/start", {"stage": "racked"})
    rack = racked["steps"][0]["result"]["instruments"]
    assert rack and racked["rack"] == rack
    for stage in ("empty", "wired", "picked"):
        _, doc = _post(play_server, "/api/demo/start", {"stage": stage})
        assert doc["stage"] == stage
        assert doc["rack"] == rack, stage
    _, state = _get(play_server, "/api/demo/stages")
    assert state["rack"] == rack and state["stage"] == "picked"


def test_starting_a_play_task_keeps_the_rack(play_server):
    _, racked = _post(play_server, "/api/demo/start", {"stage": "racked"})
    status, _ = _post(play_server, "/api/play/start", {"task": "rail-3v3", "seed": 1})
    assert status == 200
    _, state = _get(play_server, "/api/demo/stages")
    assert state["rack"] == racked["rack"]
