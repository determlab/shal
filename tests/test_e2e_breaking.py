"""shal#341 D2: the breaking-path, repeat and same-seed checks of
dev/e2e/story.py, run against REAL committed CLI output.

``tests/data/e2e/breaking/*.json`` are what the real ``shal`` / ``shal-arena``
CLIs printed (``--json``) for each path a test engineer breaks on purpose --
written by ``story.run_breaking_paths(..., out_dir=...)`` (or ``story.py
--breaking-out DIR`` in the clean-machine run), never typed by hand. No venv,
no network here: the pure ``check_*`` functions read that JSON.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
BREAKING_DIR = REPO_ROOT / "tests" / "data" / "e2e" / "breaking"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "e2e-clean-machine.yml"


def _load_story():
    spec = importlib.util.spec_from_file_location(
        "story_breaking", REPO_ROOT / "dev" / "e2e" / "story.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["story_breaking"] = mod
    spec.loader.exec_module(mod)
    return mod


story = _load_story()
PATH_IDS = [p[0] for p in story.BREAKING_PATHS]


def _fixture(path_id: str) -> dict:
    return json.loads((BREAKING_DIR / f"{path_id}.json").read_text(encoding="utf-8"))


def test_every_breaking_path_has_a_committed_output():
    assert sorted(p.stem for p in BREAKING_DIR.glob("*.json")) == sorted(PATH_IDS)
    assert len(PATH_IDS) >= 5


@pytest.mark.parametrize("path_id", PATH_IDS)
def test_breaking_path_fails_with_error_and_fix_and_no_traceback(path_id):
    rec = _fixture(path_id)
    assert rec["exit_code"] != 0
    error = rec["stdout"]["error"]
    assert error["type"] and error["message"]
    assert error["fix"].strip()
    assert "Traceback" not in rec["stderr"]
    assert "Traceback" not in json.dumps(rec["stdout"])
    result = story.check_breaking_path(path_id, rec["stdout"], rec["exit_code"],
                                       rec["stderr"], "rerun")
    assert result["result"] == "pass", result


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(exit_code=0),
    lambda r: r["stdout"]["error"].pop("fix"),
    lambda r: r["stdout"]["error"].update(fix="  "),
    lambda r: r["stdout"].update(error="plain text, not an object"),
    lambda r: r.update(stderr="Traceback (most recent call last):"),
])
def test_breaking_check_rejects_a_missing_fix_a_zero_exit_or_a_traceback(mutate):
    rec = _fixture("bad_op")
    mutate(rec)
    result = story.check_breaking_path("bad_op", rec["stdout"], rec["exit_code"],
                                       rec["stderr"], "rerun-me")
    assert result["result"] == "fail"
    assert result["log"].startswith("rerun: rerun-me")


def test_repeat_check_needs_every_run_identical_and_passing():
    ok = {"verdict": "pass", "cause": None, "record": "r1"}
    assert story.check_repeat_identical([ok] * 20, [0] * 20, 20, "r")["result"] == "pass"
    flaky = [ok] * 19 + [{"verdict": "fail", "cause": None, "record": "r2"}]
    assert story.check_repeat_identical(flaky, [0] * 19 + [1], 20, "r")["result"] == "fail"
    assert story.check_repeat_identical([ok] * 19, [0] * 19, 20, "r")["result"] == "fail"
    errored = {"verdict": "error", "cause": "transport"}
    assert story.check_repeat_identical([errored] * 20, [4] * 20, 20, "r")["result"] == "fail"


def _bench_doc(task: str, turns: float = 4) -> dict:
    side = {"runs": 10, "median_turns": turns, "turns_range": [3, 5], "correct": 10,
            "disqualified": 0, "destroyed": 0, "measurement_failures": 0,
            "sim_logs": [f"{task}/log{i}" for i in range(10)]}
    return {"ok": True, "side_effect": "write", "task": task, "runs": 10,
            "with_shal": side, "without_shal": dict(side, destroyed=10)}


def _evidence(os_label: str, doc: dict) -> dict:
    check = story.check_seed_summary(doc, 0, 7, "rerun")
    return {"os": os_label, "python": "3.13", "checks": [check]}


def test_seed_summary_ignores_machine_paths_only():
    a = story.seed_summary(_bench_doc("C:\\x\\rail-3v3.yaml"))
    b = story.seed_summary(_bench_doc("/home/runner/rail-3v3.yaml"))
    assert a == b
    assert a != story.seed_summary(_bench_doc("/home/runner/rail-3v3.yaml", turns=5))


def test_compare_seed_evidence_requires_six_identical_cells():
    cells = [_evidence(o, _bench_doc(f"/{o}/t.yaml"))
             for o in ("ubuntu", "windows", "macos") * 2]
    ok, message = story.compare_seed_evidence(cells)
    assert ok, message
    cells[3] = _evidence("windows", _bench_doc("/w/t.yaml", turns=5))
    ok, message = story.compare_seed_evidence(cells)
    assert not ok and "summaries differ" in message
    ok, message = story.compare_seed_evidence(cells[:5])
    assert not ok and "expected 6" in message
    cells[0] = {"os": "ubuntu", "python": "3.13", "checks": []}
    assert not story.compare_seed_evidence(cells)[0]


def test_workflow_runs_the_repeat_breaking_and_final_seed_compare():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "tests/test_e2e_breaking.py" in text
    assert "compare-seed" in text
    assert "download-artifact" in text
    assert "needs: story" in text
