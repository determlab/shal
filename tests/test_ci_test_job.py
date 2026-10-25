"""shal#517: the core ``test`` job in ci.yml installs shal_arena before pytest,
so tests/test_story_script.py runs instead of skipping (``importorskip``), and
fails the job if it still skips."""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _steps() -> list[dict]:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["test"]["steps"]


def _index(steps: list[dict], needle: str) -> int:
    for i, s in enumerate(steps):
        if needle in s.get("run", ""):
            return i
    raise AssertionError(f"no step runs {needle!r} in the core test job")


def test_test_job_installs_shal_arena_before_pytest():
    steps = _steps()
    assert _index(steps, "./arena") < _index(steps, "-m pytest")


def test_test_job_fails_when_story_script_tests_skip():
    steps = _steps()
    runs = [s.get("run", "") for s in steps[_index(steps, "-m pytest"):]]
    assert any("test_story_script" in r and "skip" in r for r in runs), (
        "add a step after Test that fails if test_story_script.py skips"
    )
