"""shal#343: the one-script demo of the whole story
(``examples/demos/story/run_story.py``) runs end to end and reports every
step in order.

``shal-arena`` is not a dependency of the root ``pyshal`` package (the repo's
own ``arena`` job installs it separately, same as ``pytest.importorskip("mcp")``
skips ``test_mcp_server.py`` when the optional ``mcp`` extra is absent) --
so this test skips, rather than fails, wherever ``shal_arena`` is not
installed alongside ``pyshal``.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shal_arena")

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "demos" / "story" / "run_story.py"

EXPECTED_STEPS = [
    "virtual_bench_pass",
    "virtual_bench_unplug_dmm",
    "psu_30v_blocked",
    "arena_easy",
    "arena_medium",
    "arena_hard",
    "bench_10_runs",
]


def _run_story() -> tuple[list[str], dict, int]:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--pause", "0", "--json"],
                          capture_output=True, text=True, timeout=120)
    lines = proc.stdout.splitlines()
    doc = json.loads("\n".join(lines[1:]))
    return lines, doc, proc.returncode


def test_the_first_line_is_the_fixed_disclaimer():
    lines, _doc, _ec = _run_story()
    assert lines[0] == "Simulated instruments only. Nothing here touches real hardware."


def test_the_steps_run_in_order_with_the_required_shape():
    lines, doc, ec = _run_story()
    assert ec == 0
    assert doc["ok"] is True
    assert [s["step"] for s in doc["steps"]] == EXPECTED_STEPS
    for step in doc["steps"]:
        assert set(step) == {"step", "line", "result"}
        assert step["line"]  # a plain, non-empty line printed before the step
        assert isinstance(step["result"], dict)


def test_the_unplug_step_reports_an_error_verdict():
    _lines, doc, _ec = _run_story()
    unplug = next(s for s in doc["steps"] if s["step"] == "virtual_bench_unplug_dmm")
    assert unplug["result"]["verdict"] == "error"


def test_the_pass_step_reports_a_pass_verdict():
    _lines, doc, _ec = _run_story()
    passed = next(s for s in doc["steps"] if s["step"] == "virtual_bench_pass")
    assert passed["result"]["verdict"] == "pass"


def test_the_30v_step_is_blocked_by_limits_not_sent():
    _lines, doc, _ec = _run_story()
    blocked = next(s for s in doc["steps"] if s["step"] == "psu_30v_blocked")
    assert blocked["result"]["blocked"] == "limits"


def test_the_bench_runs_show_the_gate_working():
    _lines, doc, _ec = _run_story()
    bench = next(s for s in doc["steps"] if s["step"] == "bench_10_runs")
    assert bench["result"]["with_shal_destroyed"] == 0
    assert bench["result"]["without_shal_destroyed"] > 0


def test_plain_mode_narrates_the_same_first_line_without_json():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--pause", "0"],
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert lines[0] == "Simulated instruments only. Nothing here touches real hardware."
    # plain mode never prints the machine-readable JSON document
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)
