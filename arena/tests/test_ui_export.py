"""issue #406 DoD: `--export` writes one self-contained HTML for a finished
run -- no external URL besides the repo link, the replay label is visible,
the fault is not in the static shell (only inside the embedded data, which
a finished run legitimately carries), and it needs no network to replay."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from shal_arena.errors import ArenaError
from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.export import RunNotFinished, build_export

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

REPO_URL = "https://github.com/determlab/shal"
_URL_RE = re.compile(r"https?://[^\s\"'<>]+")


def _finished_run(tmp_path: Path, given: str = "ok") -> str:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 30.0, state_dir=tmp_path)
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    answer(run_id, given, state_dir=tmp_path)
    return run_id


def test_export_refuses_a_run_that_is_not_finished(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    with pytest.raises(RunNotFinished):
        build_export(run_id, state_dir=tmp_path)
    assert issubclass(RunNotFinished, ArenaError)


def test_export_has_no_external_url_but_the_repo_link(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    html = build_export(run_id, state_dir=tmp_path, agent="claude-sonnet-5")
    urls = set(_URL_RE.findall(html))
    # the inline SVG's own xmlns is a namespace identifier, never fetched --
    # not the kind of "external URL" the DoD means (no resource it loads).
    urls.discard("http://www.w3.org/2000/svg")
    assert urls == {REPO_URL}


def test_export_shows_the_replay_label(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    html = build_export(run_id, state_dir=tmp_path, agent="claude-sonnet-5")
    assert "Replay of a recorded run" in html
    assert "claude-sonnet-5" in html
    assert "simulated instruments" in html


def test_export_never_names_a_driver_py_filename_as_text(tmp_path: Path) -> None:
    run_id = _finished_run(tmp_path)
    html = build_export(run_id, state_dir=tmp_path, agent="claude-sonnet-5")
    assert str(PASSING_DMM_DRIVER) not in html
    assert ".py" not in html


def test_export_withholds_the_fault_from_the_static_shell(tmp_path: Path) -> None:
    from shal_arena.loader import load_task
    from shal_arena.runner import pick_fault

    card = load_task(str(SAMPLE_TASK)).card
    fault = pick_fault(card, 1)   # "low_voltage" for this task/seed

    run_id = _finished_run(tmp_path, given="ok")
    html = build_export(run_id, state_dir=tmp_path)
    shell, _, rest = html.partition('<script id="run-data"')
    assert fault not in shell
    assert fault in rest   # it is in the embedded data -- the run IS finished


def test_export_never_calls_the_live_poller(tmp_path: Path) -> None:
    """The export's own driver is `startExport(...)`; it must never also
    invoke the live page's `start(...)` (its `fetch`-based poll loop), the
    one thing that would need the network."""
    run_id = _finished_run(tmp_path)
    html = build_export(run_id, state_dir=tmp_path)
    assert "startExport(" in html
    assert re.search(r"(?<!Export)start\(\s*JSON\.parse", html) is None
    assert "<script src=" not in html
    assert "<link " not in html
