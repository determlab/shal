"""tests/test_story_script.py — the one-script story (issue #343, D5).

Runs ``examples/demos/story/run_story.py --pause 0 --json`` for real and checks
the step list. The script reuses the virtual bench (needs pytest-shal) and
shal-arena; if either is not importable here the test skips, since it cannot
tell that apart from a broken script.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "examples" / "demos" / "story" / "run_story.py"

EXPECTED_STEPS = ["bench-pass", "bench-unplug", "blocked-30v",
                  "arena-easy", "arena-medium", "arena-hard", "arena-bench"]


@pytest.fixture(scope="module")
def story() -> subprocess.CompletedProcess:
    if importlib.util.find_spec("pytest_shal") is None:
        pytest.skip("pytest-shal is not installed (the virtual bench needs it)")
    return subprocess.run([sys.executable, str(SCRIPT), "--pause", "0", "--json"],
                          capture_output=True, text=True, timeout=600)


def test_story_runs_the_steps_in_order_and_exits_0(story: subprocess.CompletedProcess) -> None:
    assert story.returncode == 0, story.stderr
    doc = json.loads(story.stdout)
    assert doc["ok"] is True
    assert [s["step"] for s in doc["steps"]] == EXPECTED_STEPS
    for s in doc["steps"]:
        assert s["line"] and isinstance(s["result"], dict)
        assert s["ok"] is True, s


def test_unplug_step_has_verdict_error(story: subprocess.CompletedProcess) -> None:
    steps = {s["step"]: s for s in json.loads(story.stdout)["steps"]}
    assert steps["bench-pass"]["result"]["verdict"] == "pass"
    assert steps["bench-unplug"]["result"]["verdict"] == "error"
    assert steps["blocked-30v"]["result"]["sent"] is False
    assert steps["arena-bench"]["result"]["runs"] == 10


def test_arena_steps_run_without_the_bench(tmp_path: Path) -> None:
    """The arena half needs no pytest-shal, so it runs everywhere."""
    spec = importlib.util.spec_from_file_location("run_story", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._ensure_arena_importable()
    for name, _line, run in mod.STEPS:
        if name.startswith("bench-"):
            continue
        result, ok = run(tmp_path)
        assert ok, (name, result)
    assert [n for n, _, _ in mod.STEPS] == EXPECTED_STEPS


def test_first_line_printed_is_the_simulation_notice(story: subprocess.CompletedProcess) -> None:
    first = story.stderr.splitlines()[0]
    assert first == "Simulated instruments only. Nothing here touches real hardware."
