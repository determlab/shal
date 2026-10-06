"""shal#340 D2: the pure check functions of dev/e2e/story.py, run against a
real evidence.json (produced by the first version of the story on one OS and
committed at tests/data/e2e/sample-evidence.json — no hand-written fixture).

These tests never touch a venv, a subprocess, or the network: every check
function takes the same JSON/exit-code shape the real story already
produced, and this file just proves the function agrees with what is on
disk. A regression in the *logic* (not the *environment*) fails here, fast.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_EVIDENCE = REPO_ROOT / "tests" / "data" / "e2e" / "sample-evidence.json"


def _load_story() -> ModuleType:
    spec = importlib.util.spec_from_file_location("story", REPO_ROOT / "dev" / "e2e" / "story.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["story"] = mod
    spec.loader.exec_module(mod)
    return mod


story = _load_story()


def _evidence() -> dict:
    return json.loads(SAMPLE_EVIDENCE.read_text(encoding="utf-8"))


def _by_id(evidence: dict, check_id: str) -> dict:
    return next(c for c in evidence["checks"] if c["id"] == check_id)


def test_sample_evidence_has_the_required_shape():
    evidence = _evidence()
    assert evidence["os"]
    assert evidence["python"]
    for pkg in ("pyshal", "shal-arena", "pytest-shal", "bricks-engine"):
        v = evidence["versions"][pkg]
        assert v["version"] and v["sha"] and v["repo"] and v["filename"] and v["sha256"]
    for check in evidence["checks"]:
        assert set(check) == {"id", "result", "log"}
        assert check["result"] in ("pass", "fail")


def test_virtual_bench_pass_check_agrees_with_the_recorded_evidence():
    recorded = _by_id(_evidence(), "virtual_bench_pass")
    doc = json.loads(recorded["log"])
    assert story.check_virtual_bench_pass(doc, story.EXIT_PASS) == recorded


def test_virtual_bench_unplug_dmm_check_agrees_with_the_recorded_evidence():
    recorded = _by_id(_evidence(), "virtual_bench_unplug_dmm")
    doc = json.loads(recorded["log"])
    assert story.check_virtual_bench_unplug_dmm(doc, story.EXIT_UNREACHABLE) == recorded


def test_arena_score_file_checks_agree_with_the_recorded_evidence():
    evidence = _evidence()
    for level in story.ARENA_TASK_LEVELS:
        recorded = _by_id(evidence, f"arena_{level}_score_file")
        logged = json.loads(recorded["log"])
        answer_doc = {"score": logged["score"]}
        assert story.check_arena_score_file(
            level, logged["score_file_exists"], answer_doc) == recorded


def test_arena_bench_destroyed_check_agrees_with_the_recorded_evidence():
    recorded = _by_id(_evidence(), "arena_bench_destroyed")
    logged = json.loads(recorded["log"])
    doc = {"with_shal": {"destroyed": logged["with_shal_destroyed"]},
          "without_shal": {"destroyed": logged["without_shal_destroyed"]}}
    assert story.check_arena_bench_destroyed(doc) == recorded


def test_arena_bench_destroyed_check_catches_a_regression():
    # if the SHAL side's gate ever broke, the story's own check must fail,
    # not quietly report the old "pass".
    doc = {"with_shal": {"destroyed": 3}, "without_shal": {"destroyed": 10}}
    assert story.check_arena_bench_destroyed(doc)["result"] == "fail"
    doc = {"with_shal": {"destroyed": 0}, "without_shal": {"destroyed": 0}}
    assert story.check_arena_bench_destroyed(doc)["result"] == "fail"


def test_virtual_bench_pass_check_catches_a_wrong_exit_code():
    doc = {"verdict": "pass", "cause": None}
    assert story.check_virtual_bench_pass(doc, 1)["result"] == "fail"


def test_virtual_bench_unplug_check_catches_a_verdict_that_is_not_an_error():
    doc = {"verdict": "pass", "cause": None}
    assert story.check_virtual_bench_unplug_dmm(doc, story.EXIT_UNREACHABLE)["result"] == "fail"


def test_wheel_installed_check_agrees_with_the_recorded_evidence():
    recorded = _by_id(_evidence(), "wheel_installed_bricks-engine")
    assert recorded["result"] == "pass"
    assert story.check_wheel_installed("bricks-engine", "0.5.0", True, "0.5.0\n") == recorded
