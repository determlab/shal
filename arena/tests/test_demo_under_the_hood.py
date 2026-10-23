"""issue #507: the demo's "Under the hood" button. One test per Done-when line.

`shal-arena demo --json --trace` gives every step `under_the_hood: {yaml,
calls, gate}`; the page's button draws exactly that object (it comes from the
same `/api/demo/start` run, with no second copy in the page). The panel's
script is the shipped `_PLAY_SCRIPT`, run in the same Node DOM stub the other
UI tests use."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from shal_arena import cli
from shal_arena.ui.page import _PLAY_SCRIPT, _PLAY_STYLE, _SCRIPT, render_play_page

from .test_demo_start_picker import _page, play_server  # noqa: F401 - fixture
from .test_ui_play import (
    _clean_registry_slots,  # noqa: F401 - autouse fixture
    _post,
)
from .test_ui_timeline_no_exchange import _DOM_STUB

_KEYS = {"yaml", "calls", "gate"}


def _demo(*argv: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["demo", "--json", "--trace", *argv])
    return json.loads(out.getvalue())


@pytest.fixture(scope="module")
def story() -> dict:
    return _demo()


@pytest.fixture(scope="module")
def ran() -> dict:
    return _demo("--from", "empty")  # all 5 steps: empty, picked, racked, wired, ran


def _step(doc: dict, name: str) -> dict:
    return next(s for s in doc["steps"] if s["step"] == name)


def _node_run(steps: list[dict], tail: str) -> dict:
    """The page's real `_SCRIPT` + `_PLAY_SCRIPT`, `openHood(steps)`, then
    `tail`; returns what the script printed as JSON."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (_DOM_STUB + _SCRIPT + _PLAY_SCRIPT
              + "\nconst steps = " + json.dumps(steps) + ";"
              + "\nopenHood(steps);"
              + "\nconst out = {};\n" + tail + "\nconsole.log(JSON.stringify(out));")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "hood.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True,
                              encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


# DoD 1: --json --trace gives every step under_the_hood {yaml, calls, gate}.


def _check_shape(step: dict) -> None:
    hood = step["under_the_hood"]
    assert set(hood) == _KEYS, step["step"]
    for line in hood["yaml"]:
        assert set(line) == {"n", "text", "node"}
    for call in hood["calls"]:
        assert set(call) == {"node", "op", "args", "result"}
    assert set(hood["gate"]) == {"decision", "reason"}
    assert hood["gate"]["decision"] in ("allowed", "blocked", "none")
    assert hood["gate"]["reason"]


def test_every_step_of_the_story_has_under_the_hood(story):
    assert [s["step"] for s in story["steps"]][:3] == [
        "virtual_bench_pass", "virtual_bench_unplug_dmm", "psu_30v_blocked"]
    for step in story["steps"]:
        _check_shape(step)


def test_every_stage_step_has_under_the_hood(ran):
    assert [s["step"] for s in ran["steps"]] == ["empty", "picked", "racked", "wired", "ran"]
    for step in ran["steps"]:
        _check_shape(step)


def test_without_trace_there_is_no_key():
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["demo", "--from", "ran", "--json"])
    assert all("under_the_hood" not in s for s in json.loads(out.getvalue())["steps"])


def test_trace_without_json_is_refused_with_a_fix(capsys):
    assert cli.main(["demo", "--trace"]) != 0
    assert "--json" in capsys.readouterr().err


def test_yaml_is_the_setup_lines_the_step_uses_and_calls_reach_the_scpi_command(story):
    bench = _step(story, "virtual_bench_pass")["under_the_hood"]
    text = "\n".join(line["text"] for line in bench["yaml"])
    assert "psu0:" in text and "dmm0:" in text and "shal,sim-scpi" in text
    assert [(c["node"], c["op"]) for c in bench["calls"]] == [
        ("psu0", "set_voltage"), ("dmm0", "measure_voltage")]
    assert bench["calls"][0]["args"] == {"volts": 3.3}
    assert bench["calls"][0]["result"]["wire"][0].startswith("VOLT 3.3")
    assert bench["calls"][1]["result"]["wire"][0].startswith("MEAS:VOLT? -> ")
    assert bench["calls"][1]["result"]["result"] == _step(story, "virtual_bench_pass")[
        "result"]["volts"]
    # a step that only reads the dmm uses the dmm's lines, not the psu's
    unplug = _step(story, "virtual_bench_unplug_dmm")["under_the_hood"]
    assert {line["node"] for line in unplug["yaml"]} == {None, "dmm0"}
    # an arena step: the task yaml's lines and the run's own bus traffic
    easy = _step(story, "arena_easy")["under_the_hood"]
    assert any("address: dmm0" in line["text"] for line in easy["yaml"])
    assert any(w.startswith("MEAS:VOLT") for c in easy["calls"]
               for w in c["result"]["wire"])


# DoD 2: the button shows exactly this object, one data source.


def test_the_start_response_carries_the_cli_object_and_the_page_embeds_none(
        play_server):  # noqa: F811 - the imported fixture
    status, doc = _post(play_server, "/api/demo/start", {"stage": "ran"})
    assert status == 200
    cli_doc = _demo("--from", "ran")
    for served, printed in zip(doc["steps"], cli_doc["steps"], strict=True):
        assert served["under_the_hood"] == printed["under_the_hood"]
    page = _page(play_server)
    assert 'id="hood-btn"' in page and "Under the hood" in page
    assert "under_the_hood" not in page.replace(_PLAY_SCRIPT, "")


def test_the_button_shows_the_same_gate_decision(ran):
    step = _step(ran, "ran")
    out = _node_run(ran["steps"], f"""
selectHoodStep({ran["steps"].index(step)});
out.gate = document.getElementById("hood-gate").innerHTML;
out.btn = document.getElementById("hood-btn").hidden;""")
    decision = step["under_the_hood"]["gate"]["decision"]
    assert decision == "blocked"
    assert f'data-decision="{decision}"' in out["gate"]
    assert step["under_the_hood"]["gate"]["reason"] in out["gate"]
    assert out["btn"] is False


# DoD 3: three views from the same run.


def test_panel_has_three_views_from_the_same_run(ran):
    out = _node_run(ran["steps"], """
selectHoodStep(4);
for (const v of ["yaml", "calls", "gate"]) {
  out[v] = document.getElementById("hood-" + v).textContent;
}
setHoodView("calls");
out.shown = ["yaml", "calls", "gate"].filter(v => !document.getElementById("hood-" + v).hidden);""")
    hood = ran["steps"][4]["under_the_hood"]
    assert hood["yaml"][0]["text"].strip() in out["yaml"]
    assert hood["calls"][-1]["op"] in out["calls"]
    assert hood["gate"]["reason"] in out["gate"]
    assert out["shown"] == ["calls"]
    page = render_play_page([], None)
    for view in ("hood-yaml", "hood-calls", "hood-gate"):
        assert f'id="{view}"' in page


# DoD 4: clicking a step marks the same node in the YAML and the calls.


def test_clicking_a_step_marks_the_same_node_in_yaml_and_calls(ran):
    out = _node_run(ran["steps"], """
document.getElementById("hood-steps").children[3].onclick();   // wired
out.yaml = document.getElementById("hood-yaml").children.map(c => [c.className, c.textContent]);
out.calls = document.getElementById("hood-calls").children.map(
  c => [c.className, c.getAttribute("data-node")]);
document.getElementById("hood-calls").children[0].onclick();   // focus the first call
out.yaml0 = document.getElementById("hood-yaml").children.map(c => c.className);
out.calls0 = document.getElementById("hood-calls").children.map(c => c.className);""")
    wired = ran["steps"][3]["under_the_hood"]
    first, last = wired["calls"][0]["node"], wired["calls"][-1]["node"]
    assert first != last
    # on a click the last call's node is marked in both views
    assert {c[1] for c in out["calls"] if "mark" in c[0]} == {last}
    marked = [c[1] for c in out["yaml"] if "mark" in c[0]]
    assert marked and any(f"{last}:" in text for text in marked)
    assert not any(f"{first}:" in text for text in marked)
    # clicking a call moves the mark to its node, in both views at once
    marked0 = [i for i, cls in enumerate(out["yaml0"]) if "mark" in cls]
    assert marked0 and all(wired["yaml"][i]["node"] == first for i in marked0)
    assert [("mark" in cls) for cls in out["calls0"]] == [
        c["node"] == first for c in wired["calls"]]


# DoD 5: a blocked command is red and is not sent.


def test_a_blocked_command_is_not_sent(story, ran):
    for step in (_step(story, "psu_30v_blocked"), _step(ran, "ran")):
        hood = step["under_the_hood"]
        blocked = [c for c in hood["calls"] if c["result"].get("rejected")]
        assert len(blocked) == 1 and hood["gate"]["decision"] == "blocked"
        result = blocked[0]["result"]
        assert result["sent"] is False and result["wire"] == []
        assert "nothing was sent" in hood["gate"]["reason"]
    assert _step(ran, "ran")["result"]["gate"]["sent"] is False
    assert all(c["result"]["sent"] for c in _step(story, "virtual_bench_pass")[
        "under_the_hood"]["calls"])


def test_a_blocked_command_shows_in_red(ran):
    out = _node_run(ran["steps"], """
selectHoodStep(4);
out.calls = document.getElementById("hood-calls").children.map(c => c.className);
out.gate = document.getElementById("hood-gate").children[0].className;
selectHoodStep(3);
out.reads = document.getElementById("hood-calls").children.map(c => c.className);
out.gate_ok = document.getElementById("hood-gate").children[0].className;""")
    blocked = [c for c in out["calls"] if "blocked" in c]
    assert len(blocked) == 1
    assert "blocked" in out["gate"]
    assert not any("blocked" in c for c in out["reads"])
    assert "blocked" not in out["gate_ok"]
    assert ".hood-call.blocked" in _PLAY_STYLE and "var(--bad)" in _PLAY_STYLE


def test_the_panel_keeps_the_simulated_label():
    assert "simulated run" in render_play_page([], None)
