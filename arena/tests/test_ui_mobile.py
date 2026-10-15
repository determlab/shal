"""issue #406 DoD: viewport meta present, no drag-only control, tap targets
at least 44 px -- checked on both the live page and the export, since the
mobile rules apply to whichever one a person actually opens on a phone."""
from __future__ import annotations

import re
from pathlib import Path

from shal_arena.runner import answer, drive_input, start_run, take_measurement
from shal_arena.ui.data import run_payload
from shal_arena.ui.export import build_export
from shal_arena.ui.page import render_play_page, render_watch_page
from shal_arena.ui.play import list_play_tasks

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


_PHONE_WIDTH = 390


def _svg_render_scale(page: str) -> float:
    """The SVGs' own text is sized in viewBox units, not CSS px -- issue
    #406 CTO review round 3: computed from the real layout, not assumed.
    issue #447: both SVGs (the live visual and the card diagram) sit in a
    `.box` inside the `.page` gutter, and both draw on a 480-unit-wide
    viewBox at the least (a wider card only scales its text down further
    on a phone, so the narrowest is the case the floor is checked on)."""
    gutter = int(re.search(r"\.page \{[^}]*padding: \d+px (\d+)px", page).group(1))
    box = re.search(r"\.box \{[^}]*padding: (\d+)px", page).group(1)
    border = re.search(r"\.box \{[^}]*border: (\d+)px", page).group(1)
    assert 'viewBox="0 0 480 ' in page            # the live visual
    assert "Math.max(480, " in page               # the card diagram, 480 at the least
    rendered_width = _PHONE_WIDTH - 2 * gutter - 2 * int(box) - 2 * int(border)
    return rendered_width / 480


def test_every_phone_text_size_is_13px_or_more(tmp_path: Path) -> None:
    # every explicit font-size in the stylesheet, CTO review round 2's own
    # floor ("All phone text 13 px or more") -- round 3: SVG text must
    # clear 13px AFTER scaling to its rendered width, not by its raw
    # viewBox-unit CSS number.
    for page in _pages(tmp_path):
        scale = _svg_render_scale(page)
        style = page[page.index("<style>"):page.index("</style>")]
        for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", style):
            selector, body = rule.group(1), rule.group(2)
            for size in (int(m) for m in re.findall(r"font-size:\s*(\d+)px", body)):
                actual = size * scale if ".viz" in selector else size
                assert actual >= 13, f"{selector.strip()!r}: {actual:.1f}px actual < 13px"


# --------------------------------------------------------------------------- #
# issue #407: the Play page's buttons -- the task picker, every instrument
# control, the answer grid and "Play another task". Same phone rules as the
# WATCH page: tap targets at least 44 px, nothing that needs a drag.
# --------------------------------------------------------------------------- #

def _play_page() -> str:
    return render_play_page(list_play_tasks(), None)


def _min_heights(style: str, selector_part: str) -> list[int]:
    out = []
    for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", style):
        if selector_part in rule.group(1):
            out += [int(m) for m in re.findall(r"min-height:\s*(\d+)px", rule.group(2))]
    return out


def test_play_page_has_the_viewport_meta() -> None:
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in _play_page()


def test_play_page_has_no_drag_control() -> None:
    lowered = _play_page().lower()
    for marker in _DRAG_MARKERS:
        assert marker not in lowered
    # a slider is a drag: the voltage is a typed number plus a tap
    assert 'type="range"' not in lowered and '"range"' not in lowered
    assert 'input.type = "number"' in _play_page()


def test_every_play_button_is_a_44px_tap_target() -> None:
    page = _play_page()
    style = page[page.index("<style>"):page.index("</style>")]
    # the base rule every <button> on the page inherits
    assert re.search(r"(^|\n)button \{[^}]*min-height: 44px", style)
    # every Play-only button class, and the voltage field next to its button
    for part in ("button.task-btn", "button.ctl-btn", "button.choice", "button.again",
                 ".ctl input"):
        heights = _min_heights(style, part)
        assert heights and min(heights) >= 44, (part, heights)


def test_every_play_button_class_used_in_the_script_is_styled() -> None:
    """A new button class in the Play script must come with its own 44 px
    rule -- this catches one added later without one."""
    page = _play_page()
    script = page[page.index("</style>"):]
    used = set(re.findall(r'el\("button", "([a-z-]+)', script))
    assert used, "the Play script builds its buttons with el(\"button\", ...)"
    style = page[page.index("<style>"):page.index("</style>")]
    for cls in used:
        assert _min_heights(style, f"button.{cls}"), cls


def test_play_text_is_13px_or_more() -> None:
    page = _play_page()
    style = page[page.index("<style>"):page.index("</style>")]
    for rule in re.finditer(r"([^{}]+)\{([^}]*)\}", style):
        if ".viz" in rule.group(1):
            continue   # SVG text: scaled, checked above on the WATCH pages
        for size in (int(m) for m in re.findall(r"font-size:\s*(\d+)px", rule.group(2))):
            assert size >= 13, (rule.group(1).strip(), size)
