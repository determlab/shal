"""`--json` on `shal probe`, `shal tools` and `shal docs --list` (shal#185).

Each prints ONE JSON document on stdout. An error keeps today's exit code and
today's stderr message, and stdout holds `{"ok": false, "error": <message>}` —
as `shal call --json` does. Without --json, the output is byte-identical to what
it was before (the README Quick Start job, #159, matches probe's output exactly).
Every test runs the real command in a fresh process and parses what it printed.
"""
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

# The README Quick Start topology, as printed there.
_SIM_YAML = textwrap.dedent("""\
    # sim.yaml
    shal_version: 1
    root:
      bus:
        driver: shal,sim-i2c
        address: sim0
        children:
          temp0:
            id: ambient_temp
            driver: shal,sim-sensor
            address: 0x48
    """)

_BAD_YAML = "shal_version: 1\nroot:\n  x: {driver: 'no,such'}\n"


def _run(*argv: str, cwd, module: str = "shal.cli") -> subprocess.CompletedProcess:
    # the legacy `shal-mcp` entry does not set its own stdout to UTF-8 (only `shal`
    # does); the env makes both print the footer's dash the same way on Windows
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run([sys.executable, "-m", module, *argv], cwd=cwd, env=env,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    (tmp_path / "bad.yaml").write_text(_BAD_YAML, encoding="utf-8")
    return tmp_path


def _doc(r: subprocess.CompletedProcess) -> dict:
    return json.loads(r.stdout)  # the whole of stdout is one JSON document


# -- without --json: unchanged, byte for byte ------------------------------------

_PROBE_TEXT = (
    "# 1 read(s), 1 write(s) on this topology\n"
    "ambient_temp__read_celsius: <reading>\n"
    "# writes — not run by `shal probe`; use `shal call` (gated ops are refused "
    "until approved): ambient_temp__set_target\n")

_TOOLS_TEXT = (
    "  ambient_temp__read_celsius   [read ] Read the simulated temperature now. "
    "It drifts, so each rea\n"
    "  ambient_temp__set_target     [gated] Set the temperature the simulated room "
    "drifts toward. It c\n"
    "  shal_approve                 [gated] Confirm and execute a hardware action "
    "that is waiting for \n"
    "  shal_deny                    [write] Refuse a hardware action that is waiting "
    "for human approva\n")


def _mask_reading(text: str) -> str:
    # the sim drifts: the reading is the only part that may differ between runs
    return re.sub(r"^(ambient_temp__read_celsius): -?\d+\.\d+$", r"\1: <reading>",
                  text, flags=re.M)


def test_probe_text_is_unchanged(lab):
    r = _run("probe", "sim.yaml", cwd=lab)
    assert r.returncode == 0 and r.stderr == ""
    assert _mask_reading(r.stdout) == _PROBE_TEXT


def test_legacy_shal_mcp_probe_text_is_unchanged(lab):
    r = _run("sim.yaml", "--probe", cwd=lab, module="shal.mcp.server")
    assert r.returncode == 0
    assert _mask_reading(r.stdout) == _PROBE_TEXT


def test_tools_text_is_unchanged(lab):
    r = _run("tools", "sim.yaml", cwd=lab)
    assert r.returncode == 0 and r.stderr == ""
    assert r.stdout == _TOOLS_TEXT


def test_probe_named_read_text_is_unchanged(lab):
    r = _run("probe", "sim.yaml", "ambient_temp__read_celsius", cwd=lab)
    assert r.returncode == 0
    assert re.fullmatch(r"-?\d+\.\d+\n", r.stdout)


# -- shal probe --json ------------------------------------------------------------

def test_probe_json_shape(lab):
    r = _run("probe", "sim.yaml", "--json", cwd=lab)
    assert r.returncode == 0 and r.stderr == ""
    doc = _doc(r)
    assert set(doc) == {"ok", "topology", "reads", "writes_not_run"}
    assert doc["ok"] is True and doc["topology"] == "sim.yaml"
    [read] = doc["reads"]
    assert set(read) == {"tool", "device", "op", "ok", "value", "unit"}
    assert read["tool"] == "ambient_temp__read_celsius"
    assert (read["device"], read["op"], read["ok"], read["unit"]) == \
        ("ambient_temp", "read_celsius", True, "celsius")
    assert isinstance(read["value"], float)
    # the writes are the list the text footer names, with how to run each
    assert doc["writes_not_run"] == [{
        "tool": "ambient_temp__set_target", "device": "ambient_temp",
        "op": "set_target", "side_effect": "config", "gated": True,
        "run_with": "shal call sim.yaml ambient_temp set_target celsius=<celsius>"}]


def test_probe_json_named_read(lab):
    r = _run("probe", "sim.yaml", "ambient_temp__read_celsius", "--json", cwd=lab)
    assert r.returncode == 0
    doc = _doc(r)
    assert [x["tool"] for x in doc["reads"]] == ["ambient_temp__read_celsius"]
    assert doc["reads"][0]["ok"] is True


def test_probe_json_run_with_carries_drivers(tmp_path):
    (tmp_path / "json_rig_driver.py").write_text(textwrap.dedent('''\
        from shal import Driver, idempotent, op, register

        @register
        class JsonRig(Driver):
            compatible = "test,json-rig"
            kind = None
            llm_ready = True

            @idempotent
            @op("Read the level.", side_effect="none")
            def level(self) -> int:
                return 11

            @op("Read a register.", side_effect="none")
            def reg(self, n: int) -> int:
                return n

            @op("Clear the level.", side_effect="write")
            def clear(self) -> None:
                pass
        '''), encoding="utf-8")
    (tmp_path / "rig.yaml").write_text(
        "shal_version: 1\nroot:\n  rig: {id: rig, driver: 'test,json-rig', address: a}\n",
        encoding="utf-8")
    r = _run("probe", "rig.yaml", "--drivers", "json_rig_driver.py", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    doc = _doc(r)
    # a read that needs a value is not run, as in the text snapshot
    assert [(x["tool"], x["value"], x["unit"]) for x in doc["reads"]] == \
        [("rig__level", 11, None)]
    assert doc["writes_not_run"] == [{
        "tool": "rig__clear", "device": "rig", "op": "clear", "side_effect": "write",
        "gated": False,
        "run_with": "shal call rig.yaml rig clear --drivers json_rig_driver.py"}]


# A write whose required values are one positional-or-keyword and one keyword-only
# parameter: run_with names them (name=<name>), so their order cannot misassign one.
_SPACE_DRIVER = textwrap.dedent('''\
    import pathlib

    from shal import Driver, op, register

    @register
    class SpaceRig(Driver):
        compatible = "test,space-rig"
        kind = None
        llm_ready = True

        @op("Put a value.", side_effect="write")
        def put(self, count: int, *, label: str, gain: float = 1.0,
                loud: bool = False) -> None:
            pathlib.Path(self.addr).write_text(f"{count} {label} {gain} {loud}")
    ''')


@pytest.fixture
def space_lab(tmp_path):
    (tmp_path / "sp ace").mkdir()
    (tmp_path / "my drivers").mkdir()
    (tmp_path / "my drivers" / "space_rig_driver.py").write_text(_SPACE_DRIVER,
                                                                 encoding="utf-8")
    (tmp_path / "sp ace" / "rig.yaml").write_text(
        "shal_version: 1\nroot:\n"
        "  rig: {id: rig, driver: 'test,space-rig', address: marks.txt}\n",
        encoding="utf-8")
    return tmp_path


def _space_run_with(lab, topology: str) -> str | None:
    r = _run("probe", topology, "--drivers", "my drivers/space_rig_driver.py", "--json",
             cwd=lab)
    assert r.returncode == 0, r.stderr
    [write] = _doc(r)["writes_not_run"]
    return write["run_with"]


def test_probe_json_run_with_quotes_paths_with_spaces(space_lab):
    run_with = _space_run_with(space_lab, "sp ace/rig.yaml")
    assert run_with == ('shal call "sp ace/rig.yaml" rig put count=<count> label=<label> '
                        '--drivers "my drivers/space_rig_driver.py"')


def test_probe_json_run_with_runs_when_pasted_into_a_shell(space_lab):
    """The line, pasted into the platform shell (sh on POSIX, cmd on Windows) and
    into PowerShell where there is one, runs the op with the right values."""
    import shutil
    run_with = _space_run_with(space_lab, "sp ace/rig.yaml")
    assert run_with.startswith("shal call ")
    # fill the placeholders; `shal` is this interpreter's module (it may not be on PATH)
    rest = run_with[len("shal "):].replace("<count>", "5").replace("<label>", "hi")
    marks = space_lab / "marks.txt"
    runs = [("platform shell", f'"{sys.executable}" -m shal.cli {rest}', True)]
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if ps:
        runs.append(("powershell", [ps, "-NoProfile", "-Command",
                                    f'& "{sys.executable}" -m shal.cli {rest}'], False))
    for name, cmd, shell in runs:
        marks.unlink(missing_ok=True)
        r = subprocess.run(cmd, shell=shell, cwd=space_lab, capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        assert r.returncode == 0, (name, cmd, r.stdout, r.stderr)
        assert marks.read_text() == "5 hi 1.0 False", name


def test_call_takes_name_value_for_every_param_shape(space_lab):
    # the name=value form run_with uses, for each type `shal call` converts and
    # for a keyword-only parameter, given out of the op's order
    r = _run("call", "sp ace/rig.yaml", "rig", "put", "loud=true", "gain=2.5",
             "label=x y", "count=0x10", "--drivers", "my drivers/space_rig_driver.py",
             cwd=space_lab)
    assert r.returncode == 0, r.stderr
    assert (space_lab / "marks.txt").read_text() == "16 x y 2.5 True"


def test_probe_json_run_with_is_null_when_no_quoting_is_safe(space_lab):
    # inside double quotes bash and PowerShell still expand $, and cmd expands %
    (space_lab / "sp ace").rename(space_lab / "a$b")
    assert _space_run_with(space_lab, "a$b/rig.yaml") is None


# -- errors: same exit and stderr as without --json, a JSON error on stdout --------

@pytest.mark.parametrize("argv", [
    ("probe", "sim.yaml", "nope"),                      # unknown tool
    ("probe", "sim.yaml", "ambient_temp__set_target"),  # a write named in probe
    ("probe", "missing.yaml"),                          # no such topology
    ("tools", "missing.yaml"),
])
def test_json_error_mirrors_text_error(lab, argv):
    text = _run(*argv, cwd=lab)
    js = _run(*argv, "--json", cwd=lab)
    assert text.returncode == js.returncode == 1
    assert js.stderr == text.stderr and js.stderr.strip()
    assert _doc(js) == {"ok": False, "error": text.stderr.rstrip("\n")}


@pytest.mark.parametrize("cmd", ["probe", "tools"])
def test_json_bad_topology_is_a_json_error(lab, cmd):
    r = _run(cmd, "bad.yaml", "--json", cwd=lab)
    assert r.returncode == 1  # the same exit as the text form's traceback
    doc = _doc(r)
    assert doc["ok"] is False
    assert doc["error"].startswith(f"shal {cmd}: cannot load bad.yaml: LoadError: ")
    assert "Traceback" not in r.stderr and doc["error"] in r.stderr


# -- shal tools --json --------------------------------------------------------------

def test_tools_json_shape(lab):
    r = _run("tools", "sim.yaml", "--json", cwd=lab)
    assert r.returncode == 0 and r.stderr == ""
    doc = _doc(r)
    assert set(doc) == {"ok", "topology", "tools"} and doc["ok"] is True
    by = {t["tool"]: t for t in doc["tools"]}
    # device ops only: the MCP host's approve/deny tools are not device tools
    assert list(by) == ["ambient_temp__read_celsius", "ambient_temp__set_target"]
    for t in doc["tools"]:
        assert set(t) == {"tool", "device", "op", "kind", "side_effect", "gated",
                          "idempotent", "unit", "description", "input_schema"}
    rd, wr = by["ambient_temp__read_celsius"], by["ambient_temp__set_target"]
    assert (rd["kind"], rd["side_effect"], rd["gated"]) == ("read", "none", False)
    assert (wr["kind"], wr["side_effect"], wr["gated"]) == ("gated", "config", True)
    assert wr["device"] == "ambient_temp" and wr["op"] == "set_target"
    # the full description, not the 58-character column of the text view
    assert len(wr["description"]) > 58
    assert wr["description"].startswith("Set the temperature the simulated room drifts")
    assert wr["input_schema"]["required"] == ["celsius"]
    assert wr["input_schema"]["properties"]["celsius"]["type"] == "number"


# -- shal docs --list --json --------------------------------------------------------

def test_docs_list_json_shape(tmp_path):
    r = _run("docs", "--list", "--json", cwd=tmp_path)
    assert r.returncode == 0 and r.stderr == ""
    doc = _doc(r)
    assert set(doc) == {"ok", "references"} and doc["ok"] is True
    by = {x["name"]: x for x in doc["references"]}
    assert {"tmp102", "sqlite", "sonos"} <= set(by)
    for ref in doc["references"]:
        assert set(ref) == {"name", "compatible", "summary", "folder", "files",
                            "has_sim", "run_with", "print_with"}
        assert ref["files"][0] == "driver.py" and "topology.yaml" in ref["files"]
        assert ref["has_sim"] == ("sim.py" in ref["files"])
        assert ref["print_with"] == f"shal docs --example {ref['name']}"
    tmp = by["tmp102"]
    assert tmp["compatible"] == "ti,tmp102"
    assert tmp["summary"].startswith("ti,tmp102")
    assert tmp["files"] == ["driver.py", "sim.py", "test_tmp102.py", "topology.yaml"]
    assert tmp["run_with"] == "shal probe topology.yaml --drivers driver.py --drivers sim.py"
    sql = by["sqlite"]  # its twin is its address: no sim.py
    assert sql["compatible"] == "sqlite,database" and sql["has_sim"] is False
    assert "sim.py" not in sql["files"]
    assert sql["run_with"] == "shal probe topology.yaml --drivers driver.py"


def test_docs_json_needs_list(tmp_path):
    r = _run("docs", "--json", cwd=tmp_path)
    assert r.returncode == 2 and r.stdout == ""
    assert "--json works only with --list" in r.stderr
