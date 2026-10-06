"""issue #403 CTO review round 2/3: a REAL seed sweep -- `noise` can only be
proven correct by actually running the diagnosis across many seeds, through
the real `shal_arena`. Simulator only: this interpreter's own subprocess
calls, no network, no real hardware.

Lives under `arena/tests/`, not `tests/test_e2e_story.py` (round 3 CTO
review): the root `test` CI job collects everything under `tests/`
(`testpaths = ["tests"]`) and has no `shal_arena` installed, so this sweep
must run where `shal_arena` actually is -- the `arena` job, which runs
`pytest arena/tests -q`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_story() -> ModuleType:
    spec = importlib.util.spec_from_file_location("story", REPO_ROOT / "dev" / "e2e" / "story.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["story"] = mod
    spec.loader.exec_module(mod)
    return mod


story = _load_story()


@pytest.mark.parametrize("level", story.ARENA_TASK_LEVELS)
def test_every_seed_0_to_29_is_correctly_diagnosed(level, tmp_path):
    failures = []
    for seed in range(30):
        result = story.run_arena_task_score_file(
            sys.executable, level, tmp_path / level / str(seed), seed=seed)
        if result["result"] != "pass":
            failures.append((seed, result["log"]))
    assert not failures, failures
