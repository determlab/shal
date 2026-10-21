"""issue #507: the demo's "Under the hood" button -- the setup YAML with the
step marked, the calls down to the real SCPI command, and the gate -- all
three from ONE object, `under_the_hood: {yaml, calls, gate}`, which
`shal-arena demo --json --trace` puts on every step. One test per Done-when
line. The UI half runs the page's real shipped script in the Node DOM stub
the other UI tests use (skipped without node)."""
from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from shal_arena import cli
from shal_arena.ui.page import _PLAY_SCRIPT, _SCRIPT, _play_shell
from shal_arena.ui.server import serve

from .test_ui_play import (
    _clean_registry_slots,  # noqa: F401 - autouse fixture
    _post,
)
from .test_ui_timeline_no_exchange import _DOM_STUB

STAGES = ["empty", "picked", "racked", "wired", "ran"]


def _demo_json(*extra: str) -> dict:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        cli.main(["demo", "--json", *extra])
    return json.loads(out.getvalue())


@pytest.fixture(scope="module")
def story() -> dict:
    return _demo_json("--trace")


@pytest.fixture
def play_server(tmp_path: Path):
    httpd = serve(None, state_dir=tmp_path, port=0, open_browser=False)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _step(story: dict, name: str) -> dict:
    return next(s for s in story["steps"] if s["step"] == name)


# --- DoD 1: every step has under_the_hood: {yaml, calls, gate} ---------------


def test_every_step_has_under_the_hood_with_yaml_calls_gate(story):
    assert len(story["steps"]) == 9
    for s in story["steps"]:
        hood = s["under_the_hood"]
        assert set(hood) == {"yaml", "calls", "gate"}, s["step"]
        assert all(isinstance(line, str) for line in hood["yaml"])
        for c in hood["calls"]:
            assert set(c) == {"node", "op", "args", "result"}, (s["step"], c)
        assert set(hood["gate"]) == {"decision", "reason"}
        assert hood["gate"]["decision"] in ("allowed", "approved", "blocked")
        assert hood["gate"]["reason"]


def test_without_trace_the_key_is_absent():
    assert all("under_the_hood" not in s for s in _demo_json()["steps"])


def test_trace_without_json_is_refused(capsys):
    assert cli.main(["demo", "--trace"]) != 0
    assert "--json" in capsys.readouterr().err


def test_calls_go_down_to_the_real_scpi_command(story):
    calls = _step(story, "virtual_bench_pass")["under_the_hood"]["calls"]
    ops = [c["op"] for c in calls]
    assert ops[0] == "set_voltage" and "scpi" in ops and "measure_voltage" in ops
    scpi = [c["args"]["cmd"] for c in calls if c["op"] == "scpi"]
    assert any(cmd.startswith("VOLT") for cmd in scpi)
    assert any(cmd.startswith("MEAS:VOLT") for cmd in scpi)
    # the yaml is the bench file the step really loaded
    assert any("shal,sim-psu" in line for line in
               _step(story, "virtual_bench_pass")["under_the_hood"]["yaml"])


# --- DoD 5: a blocked command is red in the gate view and is not sent --------


def test_blocked_command_is_not_sent(story):
    hood = _step(story, "psu_30v_blocked")["under_the_hood"]
    assert hood["gate"]["decision"] == "blocked"
    assert "limits" in hood["gate"]["reason"]
    attempt = hood["calls"][0]
    assert attempt["op"] == "set_voltage" and attempt["args"] == {"volts": 30.0}
    assert "LimitError" in attempt["result"]["error"]
    # nothing reached the bus: no exchange at all follows the refused call
    assert [c for c in hood["calls"] if c["op"] in ("scpi", "exchange")] == []


def test_ran_stage_gated_op_is_blocked_and_nothing_follows_it():
    doc = _demo_json("--from", "ran", "--trace")
    hood = doc["steps"][0]["under_the_hood"]
    assert hood["gate"]["decision"] == "blocked"
    blocked = [i for i, c in enumerate(hood["calls"]) if c["op"] == "set_voltage"]
    assert len(blocked) == 1
    i = blocked[0]
    assert hood["calls"][i]["result"]["rejected"] == "approval"
    assert hood["calls"][i]["result"]["ok"] is False
    assert i == len(hood["calls"]) - 1          # nothing was sent after it
    assert doc["steps"][0]["result"]["gate"]["sent"] is False


# --- the UI: the button, three views, one data source, highlighting ---------


def _run_page(doc: dict, actions: str) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not available")
    script = (
        _DOM_STUB + "\ndocument.querySelectorAll = () => [];\n" + _SCRIPT + _PLAY_SCRIPT
        + "\nconst DOC = " + json.dumps(doc) + ";"
        + "\npost = () => Promise.resolve({status: 200, doc: DOC});"
        + """
const kids = (id) => $(id).children.map(c => ({text: c.textContent, cls: c.className,
  attrs: c._attrs}));
const snap = () => ({btn_hidden: $("hood-btn").hidden, panel_hidden: $("hood").hidden,
  steps: kids("hood-steps"), yaml: kids("hood-yaml"), calls: kids("hood-calls"),
  gate: {cls: $("hood-gate").className, decision: $("hood-gate")._attrs["data-decision"],
         text: $("hood-gate").textContent},
  label: $("hood-label").textContent});
const out = [];
$("hood-btn").onclick = toggleHood;
$("hood").hidden = true;
$("hood-btn").hidden = true;
out.push(snap());
startDemo("racked", ["empty", "picked", "racked", "wired", "ran"]);
setTimeout(() => {
  out.push(snap());
  $("hood-btn").onclick();
  out.push(snap());
""" + actions + """
  console.log(JSON.stringify(out));
}, 0);
"""
    )
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "page.js"
        path.write_text(script, encoding="utf-8")
        proc = subprocess.run([node, str(path)], capture_output=True, text=True,
                              encoding="utf-8", timeout=30)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _click_step(name: str) -> str:
    return (f"$('hood-steps').children.find(b => b._attrs['data-step'] === '{name}')"
            ".onclick(); out.push(snap());")


def test_the_page_has_the_button_and_the_panel_with_three_views():
    shell = _play_shell()
    assert 'id="hood-btn"' in shell and ">Under the hood<" in shell
    for view in ("yaml", "calls", "gate"):
        assert f'data-view="{view}"' in shell
    assert '"/api/demo/start", {stage, trace: true}' in _PLAY_SCRIPT


def test_button_opens_a_panel_whose_views_are_the_run_object(play_server):
    status, doc = _post(play_server, "/api/demo/start", {"stage": "racked", "trace": True})
    assert status == 200
    before, started, opened = _run_page(doc, "")
    assert before["btn_hidden"] is True and started["btn_hidden"] is False
    assert started["panel_hidden"] is True and opened["panel_hidden"] is False
    hood = doc["steps"][0]["under_the_hood"]
    # the three views are the object's own yaml, calls and gate -- no second copy
    assert [r["text"] for r in opened["yaml"]] == [line or " " for line in hood["yaml"]]
    assert len(opened["calls"]) == len(hood["calls"])
    for row, call in zip(opened["calls"], hood["calls"], strict=True):
        assert call["op"] in row["text"] and str(call["node"]) in row["text"]
    assert [s["text"] for s in opened["steps"]] == [s["step"] for s in doc["steps"]]
    assert "Simulated" in opened["label"]
    assert opened["gate"]["decision"] == hood["gate"]["decision"]


def test_button_shows_the_same_gate_decision_as_the_cli_for_a_step(play_server):
    status, doc = _post(play_server, "/api/demo/start", {"stage": "racked", "trace": True})
    assert status == 200
    cli_doc = _demo_json("--from", "racked", "--trace")
    *_, after = _run_page(doc, _click_step("ran"))
    cli_ran = next(s for s in cli_doc["steps"] if s["step"] == "ran")["under_the_hood"]
    assert after["gate"]["decision"] == cli_ran["gate"]["decision"] == "blocked"
    assert cli_ran["gate"]["reason"] in after["gate"]["text"]
    # a blocked command is shown in red; an allowed step is not
    assert "stop" in after["gate"]["cls"]
    assert "stop" not in _run_page(doc, "")[-1]["gate"]["cls"]


def test_clicking_a_step_marks_the_same_line_in_the_yaml_and_the_calls(play_server):
    status, doc = _post(play_server, "/api/demo/start", {"stage": "racked", "trace": True})
    assert status == 200
    ran = next(s for s in doc["steps"] if s["step"] == "ran")["under_the_hood"]
    *_, after = _run_page(doc, _click_step("ran"))
    assert [s["cls"] for s in after["steps"]].count("hood-step cur") == 1
    key = ran["calls"][0]["node"].split("/")[-1]
    hl_yaml = [r["text"] for r in after["yaml"] if "hl" in r["cls"].split()]
    hl_calls = [i for i, r in enumerate(after["calls"]) if "hl" in r["cls"].split()]
    assert hl_yaml and all(line.strip().startswith(key) for line in hl_yaml)
    assert hl_calls and all(ran["calls"][i]["node"].split("/")[-1] == key for i in hl_calls)
    assert 0 in hl_calls
    # clicking a call moves the mark to that call's line, in both views
    *_, moved = _run_page(doc, _click_step("ran") + "$('hood-calls').children[2].onclick();"
                          "out.push(snap());")
    key2 = ran["calls"][2]["node"].split("/")[-1]
    assert [r["text"].strip().split(":")[0] for r in moved["yaml"]
            if "hl" in r["cls"].split()]
    assert all(line.strip().startswith(key2) for line in
               [r["text"] for r in moved["yaml"] if "hl" in r["cls"].split()])
    assert "hl" in moved["calls"][2]["cls"].split()
