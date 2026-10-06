"""shal#343: the one-script demo of the whole story
(``examples/demos/story/run_story.py``) runs end to end and reports every
step in order, and its pure ``check_*`` functions (CTO review on #343: "add
a test that a step whose JSON disagrees makes the script exit 1") agree with
what a real run produces.

``shal-arena`` is not a dependency of the root ``pyshal`` package (the repo's
own ``arena`` job installs it separately, same as ``pytest.importorskip("mcp")``
skips ``test_mcp_server.py`` when the optional ``mcp`` extra is absent) --
so this test skips, rather than fails, wherever ``shal_arena`` is not
installed alongside ``pyshal``.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytest.importorskip("shal_arena")

import yaml  # noqa: E402 - after the importorskip, same as test_mcp_server.py's own import

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "examples" / "demos" / "story" / "run_story.py"
BENCH_YAML = REPO_ROOT / "examples" / "demos" / "virtual-bench" / "bench.yaml"

EXPECTED_STEPS = [
    "virtual_bench_pass",
    "virtual_bench_unplug_dmm",
    "psu_30v_blocked",
    "arena_easy",
    "arena_medium",
    "arena_hard",
    "arena_result_card",
    "bench_10_runs",
]


def _load_story() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_story", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["run_story"] = mod
    spec.loader.exec_module(mod)
    return mod


story = _load_story()


def _run_story() -> tuple[dict, int]:
    proc = subprocess.run([sys.executable, str(SCRIPT), "--pause", "0", "--json"],
                          capture_output=True, text=True, timeout=180)
    # CTO review on #383: with --json, stdout must be exactly one JSON
    # document and nothing else (PowerShell's ConvertFrom-Json, or any other
    # strict reader, chokes otherwise) -- so parsing the whole of stdout
    # directly, with no line stripped off first, is itself the regression
    # test for that bug.
    doc = json.loads(proc.stdout)
    return doc, proc.returncode


def test_json_mode_prints_nothing_but_the_json_document():
    doc, ec = _run_story()
    assert ec == 0
    assert doc["note"] == "Simulated instruments only. Nothing here touches real hardware."


def test_the_steps_run_in_order_with_the_required_shape():
    doc, ec = _run_story()
    assert ec == 0
    assert doc["ok"] is True
    assert [s["step"] for s in doc["steps"]] == EXPECTED_STEPS
    for step in doc["steps"]:
        assert set(step) == {"step", "line", "result"}
        assert step["line"]  # a plain, non-empty line printed before the step
        assert isinstance(step["result"], dict)


def test_the_unplug_step_reports_an_error_verdict():
    doc, _ec = _run_story()
    unplug = next(s for s in doc["steps"] if s["step"] == "virtual_bench_unplug_dmm")
    assert unplug["result"]["verdict"] == "error"


def test_the_pass_step_reports_a_pass_verdict():
    doc, _ec = _run_story()
    passed = next(s for s in doc["steps"] if s["step"] == "virtual_bench_pass")
    assert passed["result"]["verdict"] == "pass"


def test_the_30v_step_is_blocked_by_limits_not_sent():
    doc, _ec = _run_story()
    blocked = next(s for s in doc["steps"] if s["step"] == "psu_30v_blocked")
    assert blocked["result"]["blocked"] == "limits"


def test_the_scripted_steps_say_they_are_not_an_ai_agent():
    # CTO review on #383 (the CMO's addition): a viewer must not mistake a
    # scripted number for an agent's.
    doc, _ec = _run_story()
    scripted = {"arena_easy", "arena_medium", "arena_hard", "bench_10_runs"}
    for step in doc["steps"]:
        line = step["line"]
        if step["step"] in scripted:
            assert line.startswith("Scripted player, not an AI agent.")
        else:
            assert "Scripted player" not in line


def test_the_arena_steps_actually_measure_and_answer_correctly():
    # CTO review on #343: answering "ok" with no measurement is disqualified,
    # not success. Each arena step must have taken a real reading and
    # answered from it.
    doc, _ec = _run_story()
    for level in ("arena_easy", "arena_medium", "arena_hard"):
        result = next(s for s in doc["steps"] if s["step"] == level)["result"]
        assert result["reading"] is not None
        assert result["disqualified"] is False
        assert result["correct"] is True


def test_the_result_card_file_exists_after_the_run():
    doc, _ec = _run_story()
    card = next(s for s in doc["steps"] if s["step"] == "arena_result_card")["result"]
    assert Path(card["card_path"]).is_file()


def test_the_bench_runs_show_the_gate_working():
    doc, _ec = _run_story()
    bench = next(s for s in doc["steps"] if s["step"] == "bench_10_runs")
    assert bench["result"]["with_shal_destroyed"] == 0
    assert bench["result"]["without_shal_destroyed"] > 0


def test_plain_mode_narrates_the_same_first_line_without_json():
    proc = subprocess.run([sys.executable, str(SCRIPT), "--pause", "0"],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert lines[0] == "Simulated instruments only. Nothing here touches real hardware."
    # plain mode never prints the machine-readable JSON document
    with pytest.raises(json.JSONDecodeError):
        json.loads(proc.stdout)


def test_embedded_bench_topology_matches_the_checked_in_example():
    # examples/demos/virtual-bench/bench.yaml ships in no wheel yet (issue
    # #384), so run_story.py embeds the same device tree rather than reading
    # that checkout path (CTO review on #343). Parsed, not byte-for-byte --
    # the embedded copy drops the example's own comments on purpose -- so a
    # real change to the topology still fails this test, a reformatted
    # comment does not.
    embedded = yaml.safe_load(story._BENCH_TOPOLOGY_YAML)
    checked_in = yaml.safe_load(BENCH_YAML.read_text(encoding="utf-8"))
    assert embedded == checked_in


# -- the pure check_* functions: a step whose JSON disagrees with what it
# claims must flip the script's own exit code to 1 (CTO review on #343) --- #


def test_check_virtual_bench_pass_catches_a_disagreeing_verdict():
    assert story.check_virtual_bench_pass({"verdict": "pass", "volts": 3.3}) is True
    assert story.check_virtual_bench_pass({"verdict": "fail", "volts": 1.0}) is False
    assert story.check_virtual_bench_pass({"crashed": "boom"}) is False


def test_check_virtual_bench_unplug_dmm_catches_a_disagreeing_verdict():
    assert story.check_virtual_bench_unplug_dmm({"verdict": "error"}) is True
    # if the fault injection ever silently stopped faulting, this must fail,
    # not quietly report the old "pass"
    assert story.check_virtual_bench_unplug_dmm({"verdict": "pass"}) is False


def test_check_psu_30v_blocked_catches_a_request_that_was_not_blocked():
    assert story.check_psu_30v_blocked({"blocked": "limits"}) is True
    assert story.check_psu_30v_blocked({"blocked": None}) is False


def test_check_arena_task_catches_a_wrong_or_disqualified_answer():
    assert story.check_arena_task({"correct": True, "disqualified": False}) is True
    assert story.check_arena_task({"correct": False, "disqualified": False}) is False
    # answering "ok" with no measurement is disqualified -- never a pass,
    # even if the blind guess happened to also be correct
    assert story.check_arena_task({"correct": True, "disqualified": True}) is False


def test_check_arena_result_card_catches_a_missing_card_path():
    assert story.check_arena_result_card({"card_path": "/tmp/x.card.html"}) is True
    assert story.check_arena_result_card({"card_path": None}) is False
    assert story.check_arena_result_card({}) is False


def test_main_exits_1_when_a_steps_result_disagrees(monkeypatch, capsys):
    # CTO review on #383 (optional): main()'s own exit code, not just the
    # pure check_* functions in isolation -- one patched step whose result
    # fails its check must flip the real return value to 1.
    fake_step = ("fake_step", "A step engineered to disagree with its own check.",
                lambda ctx: {"ok": False}, lambda result: False)
    monkeypatch.setattr(story, "_STEPS", [fake_step])
    ec = story.main(["--pause", "0", "--json"])
    assert ec == 1
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert doc["steps"] == [{"step": "fake_step",
                             "line": "A step engineered to disagree with its own check.",
                             "result": {"ok": False}}]


def test_check_bench_10_runs_catches_a_gate_that_did_not_work():
    assert story.check_bench_10_runs(
        {"with_shal_destroyed": 0, "without_shal_destroyed": 10}) is True
    # the gate let something through
    assert story.check_bench_10_runs(
        {"with_shal_destroyed": 3, "without_shal_destroyed": 10}) is False
    # nothing destroyed on either side proves nothing about the gate
    assert story.check_bench_10_runs(
        {"with_shal_destroyed": 0, "without_shal_destroyed": 0}) is False
