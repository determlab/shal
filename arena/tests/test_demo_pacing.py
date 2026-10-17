"""issue #509: the demo's pacing rules, checked in CI. A step lasts at least
1.5 s, a full run at most 60 s, and every step has one plain sentence. A
failure names the step and the fix. (A person also tests the demo before every
merge -- see .github/pull_request_template.md.)"""
from __future__ import annotations

import contextlib
import io
import json
import time
from types import SimpleNamespace

from shal_arena import demo


def _step(step: str = "s1", ms: float = 2000.0, sentence: str = "Doing a thing.") -> dict:
    return {"step": step, "step_ms": ms, "sentence": sentence}


def test_well_paced_steps_have_no_problems():
    assert demo.pacing_problems([_step("a"), _step("b", 1500.0)]) == []


def test_step_under_minimum_names_the_step_and_the_fix():
    problems = demo.pacing_problems([_step("a"), _step("too_fast", 1499.0)])
    assert len(problems) == 1
    assert "too_fast" in problems[0] and "fix:" in problems[0] and "pause" in problems[0]


def test_run_over_maximum_names_the_slowest_step_and_the_fix():
    steps = [_step(f"s{i}", 7000.0) for i in range(9)]  # 63 s
    problems = demo.pacing_problems(steps)
    assert len(problems) == 1
    assert "63.0 s" in problems[0] and "s0" in problems[0] and "fix:" in problems[0]


def test_missing_sentence_names_the_step_and_the_fix():
    for bad in ("", "   ", None):
        problems = demo.pacing_problems([_step("silent", sentence=bad)])
        assert len(problems) == 1
        assert "silent" in problems[0] and "fix:" in problems[0] and "_STEPS" in problems[0]


def test_missing_step_ms_is_a_problem():
    assert demo.pacing_problems([{"step": "x", "sentence": "Hi."}])


def test_default_pause_is_within_the_rules():
    assert demo.DEFAULT_PAUSE_S >= demo.MIN_STEP_S
    assert demo.DEFAULT_PAUSE_S * len(demo._STEPS) < demo.MAX_RUN_S


def test_real_demo_with_default_pause_is_paced(monkeypatch):
    """The real story, default pause, read back from its own JSON. The sleep
    runs on a fake clock (no 18 s wait); the steps' real work still counts."""
    skew = {"s": 0.0}
    fake = SimpleNamespace(
        sleep=lambda s: skew.__setitem__("s", skew["s"] + s),
        monotonic=lambda: time.monotonic() + skew["s"])
    monkeypatch.setattr(demo, "time", fake)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        demo.run_story(pause=demo.DEFAULT_PAUSE_S, json_mode=True)
    steps = json.loads(out.getvalue())["steps"]

    assert len(steps) == len(demo._STEPS)
    assert all(s["sentence"] == s["line"] for s in steps)
    assert demo.pacing_problems(steps) == []
