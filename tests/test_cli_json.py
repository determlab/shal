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
from pathlib import Path

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

# The child imports THIS tree's shal, whatever the venv has installed: the tests run
# from a temp dir, so the path must be absolute.
_SRC = str(Path(__file__).resolve().parents[1] / "src")
_ENV = {**os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in (_SRC, os.environ.get("PYTHONPATH")) if p),
        # the legacy `shal-mcp` entry does not set its own stdout to UTF-8 (only
        # `shal` does); this makes both print the footer's dash the same way
        "PYTHONIOENCODING": "utf-8"}


def _run(*argv: str, cwd, module: str = "shal.cli") -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", module, *argv], cwd=cwd, env=_ENV,
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


_DOWN_DRIVER = textwrap.dedent('''\
    from shal import Driver, HopError, idempotent, op, register

    @register
    class Down(Driver):
        compatible = "test,json-down"
        kind = None
        llm_ready = True

        @idempotent
        @op("A read whose link is dead.", side_effect="none")
        def dead(self) -> int:
            raise HopError("connect failed", path=self.node.path, hop="tcp",
                           delivered="no")

        @idempotent
        @op("A read that fails.", side_effect="none")
        def broken(self) -> int:
            raise RuntimeError("check failed")
    ''')


def test_probe_json_unreachable_is_its_own_type_and_a_failing_read_is_not(tmp_path):
    (tmp_path / "down_driver.py").write_text(_DOWN_DRIVER, encoding="utf-8")
    (tmp_path / "down.yaml").write_text(
        "shal_version: 1\nroot:\n  bus:\n    driver: shal,sim-i2c\n"
        "    address: 192.0.2.9:5025\n    children:\n"
        "      d: {id: d, driver: 'test,json-down', address: 1}\n", encoding="utf-8")
    base = ("probe", "down.yaml")
    flags = ("--drivers", "down_driver.py", "--json")
    r = _run(*base, "d__dead", *flags, cwd=tmp_path)
    assert r.returncode == 4, r.stderr
    error = _doc(r)["error"]
    assert set(error) == {"type", "message", "fix"} and error["fix"]
    assert error["type"] == "Unreachable" and "192.0.2.9:5025" in error["message"]
    r = _run(*base, "d__broken", *flags, cwd=tmp_path)  # a failing check: old code
    assert r.returncode == 1, r.stderr
    assert _doc(r)["error"]["type"] == "ReadFailed"
    r = _run(*base, *flags, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    kinds = {x["op"]: x["error"]["type"] for x in _doc(r)["reads"]}
    assert kinds == {"dead": "Unreachable", "broken": "ReadFailed"}


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


def _shells() -> list[tuple[str, object]]:
    """Every shell a run_with may be pasted into here, as (name, argv-builder): the
    platform shell (sh on POSIX, cmd on Windows), bash where there is one (Git Bash
    on Windows; not the WSL launcher), and PowerShell where there is one."""
    import shutil
    py = sys.executable
    out = [("platform shell", lambda rest: f'"{py}" -m shal.cli {rest}')]
    bash = shutil.which("bash")
    if sys.platform == "win32" and bash and "system32" not in bash.lower():
        posix_py = Path(py).as_posix()
        out.append(("bash", lambda rest: [bash, "-c", f'"{posix_py}" -m shal.cli {rest}']))
    ps = shutil.which("pwsh") or shutil.which("powershell")
    if ps:
        out.append(("powershell", lambda rest: [ps, "-NoProfile", "-Command",
                                                f'& "{py}" -m shal.cli {rest}']))
    return out


def _paste(run_with: str, cwd, **values: str):
    """run_with with its placeholders filled, pasted into each shell. `shal` is this
    interpreter's module (the script may not be on PATH). Yields (shell, result)."""
    assert run_with.startswith("shal call ")
    rest = run_with[len("shal "):]
    for k, v in values.items():
        rest = rest.replace(f"<{k}>", v)
    for name, build in _shells():
        cmd = build(rest)
        yield name, subprocess.run(cmd, shell=isinstance(cmd, str), cwd=cwd, env=_ENV,
                                   capture_output=True, text=True, encoding="utf-8",
                                   timeout=60)


def test_probe_json_run_with_runs_when_pasted_into_a_shell(space_lab):
    """The line, pasted into every shell there is here, runs the op with the right
    values."""
    run_with = _space_run_with(space_lab, "sp ace/rig.yaml")
    marks = space_lab / "marks.txt"
    for name, r in _paste(run_with, space_lab, count="5", label="hi"):
        assert r.returncode == 0, (name, run_with, r.stdout, r.stderr)
        assert marks.read_text() == "5 hi 1.0 False", name
        marks.unlink()


# Each tries to make a shell run `echo x>pwned` — a command bash, cmd and PowerShell
# all run — by breaking out of run_with's quoting. The first two were reproduced by
# the reviewer against the round-2 deny-list.
_INJECTIONS = [
    ["x y\\", ";echo x>pwned;", "c d\\"],   # bash: a trailing \ escapes the closing "
    ["a\u201d; echo x>pwned; \u201db"],     # PowerShell reads curly quotes as "
    ["a\u201c; echo x>pwned; \u201eb"],
    ['a"; echo x>pwned; "b'],
    ["trail\\"],
    ["x; echo x>pwned"],
    ["x & echo x>pwned"],
    ["x ^& echo x>pwned"],
    ["x | echo x>pwned"],
    ["x $(echo x>pwned)"],
    ["x `echo x>pwned`"],
    ["x %COMSPEC%"],
    ["x !x! echo"],
    ["x (echo x>pwned)"],
    ["x 'echo x>pwned'"],
    ["caf\u00e9 dir/drv.py"],               # non-ASCII, even a harmless letter
    ["tab\there"],
    ["new\nline"],
    ["--%"],                                # PowerShell's stop-parsing token
]


@pytest.mark.parametrize("drivers", _INJECTIONS)
def test_run_with_is_null_for_anything_off_the_allow_list(drivers):
    import argparse

    from shal import cli
    fact = {"device": "rig", "op": "put"}
    args = argparse.Namespace(topology="sim.yaml", drivers=drivers)
    assert cli._call_command(args, fact, {"required": ["n"]}) is None
    # the topology is checked the same way as each --drivers value
    args = argparse.Namespace(topology=drivers[0], drivers=[])
    assert cli._call_command(args, fact, {"required": ["n"]}) is None


# The same attacks end to end: real --drivers folders with these names (a folder of
# no .py files imports nothing), `shal probe --json` in a fresh process, and its
# run_with pasted into every shell here if it is not null. No name holds > or |, so
# each folder can exist on Windows too; each payload makes a file named pwned.
_ATTACK_FOLDERS = [
    ["x y\\", ";touch pwned;", "c d\\"],    # the reviewer's bash reproduction
    ["a”; ni pwned; ”b"],         # the reviewer's PowerShell reproduction
    ["a & copy nul pwned & b"],             # cmd
    ["x ^& copy nul pwned"],
    ["a;touch pwned;b"],
    ["x $(touch pwned)"],
    ["x `touch pwned`"],
    ["x %COMSPEC%"],
    ["café dir"],
]


@pytest.mark.parametrize("folders", _ATTACK_FOLDERS)
def test_probe_json_attack_paths_paste_nothing(lab, folders):
    for f in folders:
        try:
            (lab / f).mkdir(exist_ok=True)
        except OSError:
            pytest.skip(f"this file system cannot name a folder {f!r}")
    argv = ["probe", "sim.yaml"]
    for f in folders:
        argv += ["--drivers", f]
    r = _run(*argv, "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    [write] = _doc(r)["writes_not_run"]
    if write["run_with"] is not None:
        for name, _ in _paste(write["run_with"], lab, celsius="30"):
            assert not (lab / "pwned").exists(), (name, write["run_with"])
    assert write["run_with"] is None  # every one of these is off the allow-list
    assert not (lab / "pwned").exists()


def test_run_with_paste_runs_nothing_extra(tmp_path):
    """Every character the allow-list lets through, pasted into every shell here:
    no marker file appears, and the arguments arrive intact: the topology loads, the
    --drivers folder is found, and `shal call` reaches the gate and refuses."""
    (tmp_path / "p q#1=@~").mkdir()
    (tmp_path / "p q#1=@~" / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    drivers = "d r#2=@~/a-b_c.d+e"  # a folder of no .py files: imports nothing
    (tmp_path / drivers).mkdir(parents=True)
    r = _run("probe", "p q#1=@~/sim.yaml", "--drivers", drivers, "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    [write] = _doc(r)["writes_not_run"]
    assert write["run_with"] == ('shal call "p q#1=@~/sim.yaml" ambient_temp set_target '
                                 f'celsius=<celsius> --drivers "{drivers}"')
    for name, p in _paste(write["run_with"], tmp_path, celsius="30"):
        assert not (tmp_path / "pwned").exists(), name
        assert "refused: ambient_temp.set_target" in p.stderr, (name, p.stdout, p.stderr)
    assert not (tmp_path / "pwned").exists()


# A path that starts with - (shal#197): bare, `shal call` reads it as a flag, and
# PowerShell 5.1 even splits a bare -x.yaml into -x and .yaml before the exe sees
# it. run_with writes it as ./-x.yaml, which every shell passes as one path.
@pytest.mark.parametrize("topology, drivers, shown", [
    ("-x.yaml", "-d", "./-x.yaml ambient_temp set_target celsius=<celsius> "
                      "--drivers ./-d"),
    ("-x y.yaml", "-d r", '"./-x y.yaml" ambient_temp set_target celsius=<celsius> '
                          '--drivers "./-d r"'),
])
def test_probe_json_run_with_guards_a_path_starting_with_dash(tmp_path, topology,
                                                               drivers, shown):
    (tmp_path / topology).write_text(_SIM_YAML, encoding="utf-8")
    (tmp_path / drivers).mkdir()  # a folder of no .py files: imports nothing
    r = _run("probe", f"--drivers={drivers}", "--json", "--", topology, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    [write] = _doc(r)["writes_not_run"]
    assert write["run_with"] == f"shal call {shown}"
    for name, p in _paste(write["run_with"], tmp_path, celsius="30"):
        # the topology loaded and the op reached the gate: `shal call` read the
        # path as a path, not as a flag
        assert "refused: ambient_temp.set_target" in p.stderr, (name, p.stdout, p.stderr)


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
    doc = _doc(js)
    assert doc["ok"] is False
    assert doc["error"]["message"] == text.stderr.rstrip("\n")
    assert doc["error"]["type"] and doc["error"]["fix"]


@pytest.mark.parametrize("cmd", ["probe", "tools"])
def test_json_bad_topology_is_a_json_error(lab, cmd):
    r = _run(cmd, "bad.yaml", "--json", cwd=lab)
    assert r.returncode == 1  # the same exit as the text form's traceback
    doc = _doc(r)
    assert doc["ok"] is False
    msg = doc["error"]["message"]
    assert msg.startswith(f"shal {cmd}: cannot load bad.yaml: LoadError: ")
    assert "Traceback" not in r.stderr and msg in r.stderr


@pytest.mark.parametrize("route, says", [
    ("{via: /ssh, address: 0x49}", "route ssh: no bus at /ssh"),
    ("{via: /bus, address: 0x49, name: bus}",
     "route bus via /bus and route bus via /bus share a name; set name: on one"),
])
def test_tools_json_bad_route_names_the_route_and_the_fix(lab, route, says):
    # #231 agent path: add one jump, run `shal tools --json`, read what to change
    bad = _SIM_YAML + f"        routes:\n          - {route}\n"
    (lab / "routes.yaml").write_text(bad, encoding="utf-8")
    r = _run("tools", "routes.yaml", "--json", cwd=lab)
    assert r.returncode != 0
    doc = _doc(r)
    assert doc["ok"] is False and says in doc["error"]["message"]


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
    # a node without routes: no `routes` key, no `via` argument (#237)
    assert all("via" not in t["input_schema"]["properties"] for t in doc["tools"])


_TWO_ROUTE_YAML = textwrap.dedent("""\
    shal_version: 1
    root:
      console:
        driver: shal,sim-i2c
        address: sim0
        children:
          board:
            id: board
            driver: shal,sim-sensor
            address: 0x48
            routes:
              - {via: /net, address: 0x49, name: ssh}
      net:
        driver: shal,sim-i2c
        address: sim1
    """)


def test_tools_json_lists_a_routed_node_once_with_its_routes_in_order(tmp_path):
    # #237 exit test 5: one entry per op of `board`, not one per route
    (tmp_path / "t.yaml").write_text(_TWO_ROUTE_YAML, encoding="utf-8")
    r = _run("tools", "t.yaml", "--json", cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    tools = _doc(r)["tools"]
    assert [t["tool"] for t in tools] == ["board__read_celsius", "board__set_target"]
    for t in tools:
        assert t["device"] == "board" and t["routes"] == ["console", "ssh"]
        via = t["input_schema"]["properties"]["via"]
        assert via["type"] == "string" and via["enum"] == ["console", "ssh"]
        assert "via" not in t["input_schema"].get("required", [])


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
    assert r.returncode == 2
    assert "--json works only with --list" in r.stderr
    err = json.loads(r.stdout)["error"]
    assert err["fix"] == "add --list or --samples"


@pytest.mark.parametrize("argv", [
    ("probe", "--json"),
    ("tools", "--json"),
    ("routes", "--json"),
    ("check", "--json"),
    ("records", "--verdict", "bogus", "--json"),
    ("docs", "--json"),
], ids=["probe", "tools", "routes", "check", "records", "docs"])
def test_usage_error_is_a_json_error_with_json(tmp_path, argv):
    r = _run(*argv, cwd=tmp_path)
    assert r.returncode == 2
    doc = json.loads(r.stdout)
    assert doc["ok"] is False
    assert doc["error"]["type"] == "UsageError"
    assert doc["error"]["fix"]


def test_usage_error_without_json_keeps_stdout_empty(tmp_path):
    r = _run("probe", cwd=tmp_path)
    assert r.returncode == 2 and r.stdout == ""
    assert "required" in r.stderr


def test_unrecognized_argument_is_a_json_usage_error(tmp_path):
    r = _run("tools", "x.yaml", "--bogus", "--json", cwd=tmp_path)
    assert r.returncode == 2
    assert json.loads(r.stdout)["error"]["type"] == "UsageError"


def test_version_prints_the_installed_version(tmp_path):
    r = _run("--version", cwd=tmp_path)
    assert r.returncode == 0
    assert re.match(r"^shal \d+\.\d+\.\d+", r.stdout)
