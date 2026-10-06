"""issue #406 DoD: viewport meta present, no drag-only control, tap targets
at least 44 px -- checked on both the live page and the export, since the
mobile rules apply to whichever one a person actually opens on a phone."""
from __future__ import annotations

import re
from pathlib import Path

from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import render_watch_page

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK

_DRAG_MARKERS = ("ondrag", "draggable=", "dragstart", "dragover", "interact.js")


def _pages(tmp_path: Path) -> list[str]:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    drive_input(run_id, "psu0", 30.0, state_dir=tmp_path)
    drive_input(run_id, "psu0", 5.0, state_dir=tmp_path)
    take_measurement(run_id, "dmm0", PASSING_DMM_DRIVER, state_dir=tmp_path)
    live = render_watch_page(run_id, run_payload(run_id, state_dir=tmp_path))

    answer(run_id, "ok", state_dir=tmp_path)
    export = build_export(run_id, state_dir=tmp_path)
    return [live, export]


def test_viewport_meta_present(tmp_path: Path) -> None:
    for page in _pages(tmp_path):
        assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in page


def test_no_drag_only_control(tmp_path: Path) -> None:
    for page in _pages(tmp_path):
        lowered = page.lower()
        for marker in _DRAG_MARKERS:
            assert marker not in lowered


def test_tap_targets_are_at_least_44px(tmp_path: Path) -> None:
    # `.step` is the one repeated, per-item surface on the page (one per
    # timeline entry); its own rule sets the floor every item inherits.
    for page in _pages(tmp_path):
        assert re.search(r"\.step\s*\{[^}]*min-height:\s*44px", page)


def test_every_phone_text_size_is_13px_or_more(tmp_path: Path) -> None:
    # every explicit font-size in the stylesheet, CTO review round 2's own
    # floor ("All phone text 13 px or more").
    for page in _pages(tmp_path):
        style = page[page.index("<style>"):page.index("</style>")]
        sizes = [int(m) for m in re.findall(r"font-size:\s*(\d+)px", style)]
        assert sizes, "expected at least one explicit font-size in the stylesheet"
        assert min(sizes) >= 13
