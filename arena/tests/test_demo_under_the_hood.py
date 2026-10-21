"""issue #507: the demo's "Under the hood" button.

`shal-arena demo --json --trace` puts `under_the_hood: {yaml, calls, gate}` on
every step; the demo page's button shows exactly that object (the document
`/api/demo/start` returns), in three views, with the step's blocked command
in red. One test per Done-when line. The page's own shipped `_PLAY_SCRIPT` is
run in the Node DOM stub the other UI tests use (skipped without node)."""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shal_arena import cli
from shal_arena.ui.page import _PLAY_SCRIPT, _SCRIPT, _play_shell
from shal_arena.ui.server import _demo_from

from .test_ui_timeline_no_exchange import _DOM_STUB


def _demo(argv: list[str]) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        cli.main(["demo", *argv])
    return json.loads(out.getvalue())


@pytest.fixture(scope="module")
def story() -> dict:
    return _demo(["--json", "--trace"])


def _node_run(doc: dict, js: str) -> dict:
    """Runs the shipped page scripts against ``doc`` (what `/api/demo/start`
    returned) and the JS in ``js``; returns what ``js`` logs as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (_DOM_STUB + _SCRIPT + _PLAY_SCRIPT + "\nconst doc = " + json.dumps(doc) + ";"
              + "\nconst out = (id) => ({text: document.getElementById(id).textContent,"
              + " html: document.getElementById(id).innerHTML,"
              + " cls: document.getElementById(id).className});"
              + "\n" + js)
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "uth.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True,
                              encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# 1. `shal-arena demo --json --trace`: every step has under_the_hood

def test_every_step_has_under_the_hood(story: dict) -> None:
    assert story["steps"]
    for step in story["steps"]:
        uth = step["under_the_hood"]
        assert set(uth) == {"yaml", "calls", "gate"}, step["step"]
        assert all(isinstance(line, str) for line in uth["yaml"])
        for call in uth["calls"]:
            assert set(call) == {"node", "op", "args", "result"}
        assert set(uth["gate"]) == {"decision", "reason"}
        assert uth["gate"]["decision"] in ("allowed", "blocked") and uth["gate"]["reason"]


def test_without_trace_the_steps_are_unchanged() -> None:
    doc = _demo(["--from", "ran", "--json"])
    assert all("under_the_hood" not in s for s in doc["steps"])


def test_the_data_is_the_data_of_the_run(story: dict) -> None:
    by_step = {s["step"]: s for s in story["steps"]}

    ok = by_step["virtual_bench_pass"]
    assert "      psu0:" in ok["under_the_hood"]["yaml"]
    set_v, measure = ok["under_the_hood"]["calls"]
    assert (set_v["node"], set_v["op"], set_v["args"]) == ("psu", "set_voltage", [3.3])
    # down to the real SCPI command the bus carried
    assert any("VOLT" in c.upper() for c in set_v["result"]["scpi"])
    assert "MEAS:VOLT:DC?" in measure["result"]["scpi"]
    assert measure["result"]["value"] == ok["result"]["volts"]
    assert ok["under_the_hood"]["gate"]["decision"] == "allowed"

    blocked = by_step["psu_30v_blocked"]["under_the_hood"]
    (call,) = blocked["calls"]
    assert call["args"] == [30.0]
    assert call["result"]["blocked"] is True and call["result"]["scpi"] == []  # not sent
    assert blocked["gate"]["decision"] == "blocked"
    assert by_step["psu_30v_blocked"]["result"]["message"] in blocked["gate"]["reason"]

    easy = by_step["arena_easy"]["under_the_hood"]  # read back from the run's own sim log
    assert any(c["result"]["scpi"] for c in easy["calls"])
    assert any("address: dmm0" in line for line in easy["yaml"])


def test_stage_steps_carry_it_too_and_the_gated_op_is_blocked() -> None:
    doc = _demo(["--from", "ran", "--json", "--trace"])
    by_step = {s["step"]: s["under_the_hood"] for s in doc["steps"]}
    assert set(by_step) == {"ran"}
    ran = by_step["ran"]
    assert ran["yaml"] and ran["gate"]["decision"] == "blocked"
    assert [c["result"]["blocked"] for c in ran["calls"]].count(True) == 1
    assert all(c["result"]["scpi"] for c in ran["calls"] if not c["result"]["blocked"])
    assert doc["steps"][0]["result"]["gate"]["sent"] is False


# 2. the button shows exactly this object (one data source)

def test_the_button_shows_the_same_gate_decision() -> None:
    cli_doc = _demo(["--from", "ran", "--json", "--trace"])
    served = _demo_from("ran")     # the document /api/demo/start returns
    assert [s["under_the_hood"] for s in served["steps"]] == [
        s["under_the_hood"] for s in cli_doc["steps"]]

    gate = served["steps"][0]["under_the_hood"]["gate"]
    out = _node_run(served, 'loadUth(doc); console.log(JSON.stringify({gate: out("uth-gate"),'
                            ' open: document.getElementById("uth-open").hidden}));')
    assert out["open"] is False
    assert out["gate"]["text"] == gate["decision"] + gate["reason"]


def test_page_has_the_button_and_the_panel_and_no_second_copy() -> None:
    page = _play_shell()
    assert 'id="uth-open"' in page and "Under the hood" in page
    assert 'id="uth-panel"' in page
    assert "under_the_hood" not in page      # the data comes only from the response


# 3. three views from the same run, 4. the click highlights the same line

def test_panel_has_three_views_of_the_same_run() -> None:
    page = _play_shell()
    for view in ("yaml", "calls", "gate"):
        assert f'data-view="{view}"' in page
    doc = _demo_from("ran")
    out = _node_run(doc, 'loadUth(doc); console.log(JSON.stringify({y: out("uth-yaml"),'
                         ' c: out("uth-calls"), g: out("uth-gate"), l: out("uth-label")}));')
    uth = doc["steps"][0]["under_the_hood"]
    for line in uth["yaml"]:
        assert line.strip() in out["y"]["text"]
    for call in uth["calls"]:
        assert f'{call["node"]}.{call["op"]}(' in out["c"]["text"]
    assert out["g"]["text"].startswith(uth["gate"]["decision"])
    assert "Simulated" in out["l"]["text"]      # the simulated label stays


def test_clicking_a_step_highlights_the_same_line_in_yaml_and_calls(story: dict) -> None:
    two = {**story, "steps": [s for s in story["steps"]
                              if s["step"] in ("virtual_bench_pass", "psu_30v_blocked")]}
    rows = _node_run(two, """
loadUth(doc);
const rows = [];
const snap = () => rows.push({yaml: out("uth-yaml").html, calls: out("uth-calls").html});
selectUthCall(0); snap();
selectUthCall(1); snap();
selectUthStep(1); snap();
console.log(JSON.stringify(rows));""")

    def hot_line(html: str) -> str:
        (line,) = re.findall(r'<div class="uth-line hot[^"]*"[^>]*>([^<]*)</div>', html)
        return line

    def hot_call(html: str) -> str:
        (call,) = re.findall(r'<button class="uth-call hot[^"]*"[^>]*><span class="c">'
                             r'([^<]*)</span>', html)
        return call

    # call 0, then call 1 of the first step (set_voltage, measure_voltage)
    assert "id: psu" in hot_line(rows[0]["yaml"])
    assert hot_call(rows[0]["calls"]).startswith("psu.set_voltage")
    assert "id: dmm" in hot_line(rows[1]["yaml"])
    assert hot_call(rows[1]["calls"]).startswith("dmm.measure_voltage")
    # the second step opens on its blocked call
    assert hot_call(rows[2]["calls"]).startswith("psu.set_voltage(30")
    assert "id: psu" in hot_line(rows[2]["yaml"])


# 5. a blocked command is red and was not sent

def test_blocked_command_is_red_and_not_sent(story: dict) -> None:
    step = next(s for s in story["steps"] if s["step"] == "psu_30v_blocked")
    out = _node_run({**story, "steps": [step]}, """
loadUth(doc);
console.log(JSON.stringify({calls: out("uth-calls"), gate: out("uth-gate"),
                            yaml: out("uth-yaml")}));""")
    assert 'class="uth-call hot blocked"' in out["calls"]["html"]
    assert "blocked, not sent" in out["calls"]["text"] and "sent: " not in out["calls"]["text"]
    assert out["gate"]["cls"] == "uth-gate blocked"
    assert 'class="uth-line hot bad"' in out["yaml"]["html"]
