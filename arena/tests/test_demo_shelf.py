"""issue #506: the demo's device shelf -- 12 devices in 4 groups, 4 with a
working driver, the rest grey; a grey device cannot be dragged and says what
to do next; a rack check shows what is missing; an agent reads the same
listing with `shal-arena shelf --json`."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena.errors import ArenaError
from shal_arena.replay.shelf import (
    GROUPS,
    ShelfError,
    drag,
    rack_check,
    render_shelf_page,
    shelf_devices,
    shelf_listing,
)
from shal_arena.ui.play import REFERENCE_DRIVERS


def _run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    # run the CLI from this checkout's own source, whatever else is installed
    src = str(Path(__file__).resolve().parents[1] / "src")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([src, os.environ.get("PYTHONPATH", "")])}
    return subprocess.run([sys.executable, "-m", "shal_arena.cli", *args], cwd=cwd, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)


def _present() -> list[dict]:
    return [d for d in shelf_devices() if d["driver"] == "present"]


def _grey() -> list[dict]:
    return [d for d in shelf_devices() if d["driver"] == "missing"]


def test_twelve_devices_four_with_a_driver_rest_grey() -> None:
    devices = shelf_devices()
    assert len(devices) == 12
    assert len({d["id"] for d in devices}) == 12
    assert len(_present()) == 4 and len(_grey()) == 8
    assert {d["group"] for d in devices} == {g for g, _ in GROUPS}
    assert len(GROUPS) == 4


def test_each_card_has_model_connection_status_and_line_count() -> None:
    for d in shelf_devices():
        assert d["model"] and d["connection"]
        assert d["driver"] in ("present", "missing")
    for d in _present():
        path = REFERENCE_DRIVERS[d["case"]]
        assert d["driver_lines"] == len(path.read_text(encoding="utf-8").splitlines()) > 0
    for d in _grey():
        assert d["driver_lines"] == 0 and d["case"] is None


def test_the_present_drivers_are_the_existing_reference_drivers() -> None:
    assert {d["case"] for d in _present()} == set(REFERENCE_DRIVERS)


def test_page_shows_cards_grey_ones_not_draggable_with_write_driver() -> None:
    page = render_shelf_page()
    assert page.count('class="card" draggable="true"') == 4
    assert page.count('class="card grey"') == 8
    assert page.count("Write driver") == 8
    for d in shelf_devices():
        assert d["model"] in page
    assert 'class="card grey" draggable' not in page


def test_dragging_a_grey_device_is_refused_with_the_next_step() -> None:
    grey = _grey()[0]
    with pytest.raises(ShelfError) as exc:
        drag(grey["id"])
    err = exc.value
    assert grey["model"] in err.message and "no driver" in err.message
    assert "shal docs" in err.fix and err.fix == grey["next_step"]
    assert drag(_present()[0]["id"])["driver"] == "present"
    assert "Next step:" in render_shelf_page()  # the page's refusal carries it too


def test_rack_check_shows_what_is_missing() -> None:
    ok, grey = _present()[0], _grey()[0]
    assert rack_check([ok["id"]]) == {"ok": True, "side_effect": "none",
                                      "rack": [ok["id"]], "missing": []}
    bad = rack_check([ok["id"], grey["id"]])
    assert bad["ok"] is False
    assert [m["id"] for m in bad["missing"]] == [grey["id"]]
    assert bad["missing"][0]["next_step"]


def test_unknown_device_and_empty_rack_are_refused_with_a_fix() -> None:
    for call in (lambda: drag("nope"), lambda: rack_check(["nope"]), lambda: rack_check([])):
        with pytest.raises(ArenaError) as exc:
            call()
        assert exc.value.fix


def test_listing_is_agent_readable_json_with_driver_present_or_missing(tmp_path: Path) -> None:
    proc = _run_cli("shelf", "--json", cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc == json.loads(json.dumps(shelf_listing()))
    assert doc["side_effect"] == "none" and (doc["present"], doc["missing"]) == (4, 8)
    assert [d["driver"] for d in doc["devices"]].count("present") == 4


def test_cli_rack_check_exit_codes_and_page_out(tmp_path: Path) -> None:
    ok, grey = _present()[0]["id"], _grey()[0]["id"]
    good = _run_cli("shelf", "--check", ok, "--json", cwd=tmp_path)
    assert good.returncode == 0 and json.loads(good.stdout)["ok"] is True
    bad = _run_cli("shelf", "--check", ok, grey, "--json", cwd=tmp_path)
    assert bad.returncode == 1
    assert [m["id"] for m in json.loads(bad.stdout)["missing"]] == [grey]
    unknown = _run_cli("shelf", "--check", "nope", "--json", cwd=tmp_path)
    assert unknown.returncode == 2 and json.loads(unknown.stdout)["error"]["fix"]
    out = tmp_path / "shelf.html"
    page = _run_cli("shelf", "--out", str(out), "--json", cwd=tmp_path)
    assert page.returncode == 0 and out.read_text(encoding="utf-8") == render_shelf_page()
