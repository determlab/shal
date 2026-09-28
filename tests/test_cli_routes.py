"""`shal routes` and the routes agent path (#237, routes M1 part 4; RFC-001 §2 "Pin by
name", §7, "Agent path").

`shal routes <topology> <node>` prints what the file declares for one node: each
route's name, the bus it goes via and its address, in order. A node without routes
shows its one main route. The agent path test drives `shal tools` → `shal call
--via` → `shal routes` reading only `--json` output, as a cold agent would."""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_TOPO = textwrap.dedent("""\
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
        children:
          twin: {driver: "shal,sim-sensor", address: 0x49}   # net's device at 0x49
    """)

_SIM_YAML = textwrap.dedent("""\
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

# the child imports THIS tree's shal, whatever the interpreter has installed
_SRC = str(Path(__file__).resolve().parents[1] / "src")
_ENV = {**os.environ,
        "PYTHONPATH": os.pathsep.join(p for p in (_SRC, os.environ.get("PYTHONPATH")) if p)}


def _shal(*argv: str, cwd, env=None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          env=env or _ENV,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def lab(tmp_path):
    (tmp_path / "t.yaml").write_text(_TOPO, encoding="utf-8")
    (tmp_path / "sim.yaml").write_text(_SIM_YAML, encoding="utf-8")
    return tmp_path


# ---- shal routes -------------------------------------------------------------------

def test_routes_json_prints_the_declared_routes_in_order(lab):
    r = _shal("routes", "t.yaml", "board", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout) == {
        "ok": True, "topology": "t.yaml", "device": "board", "path": "/console/board",
        "routes": [{"name": "console", "via": "/console", "address": 0x48},
                   {"name": "ssh", "via": "/net", "address": 0x49}]}


def test_routes_json_of_a_node_without_routes_is_its_one_main_route(lab):
    r = _shal("routes", "sim.yaml", "ambient_temp", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["routes"] == [{"name": "bus", "via": "/bus",
                                               "address": 0x48}]


@pytest.mark.parametrize("key", ["board", "/console/board"])
def test_routes_finds_the_node_by_id_or_path(lab, key):
    r = _shal("routes", "t.yaml", key, "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["device"] == "board"


def test_routes_text_lists_one_line_per_route(lab):
    r = _shal("routes", "t.yaml", "board", cwd=lab)
    assert r.returncode == 0, r.stderr
    lines = r.stdout.splitlines()
    assert lines[0] == "board (/console/board)"
    assert lines[1].split() == ["console", "via", "/console", "address", "72"]
    assert lines[2].split() == ["ssh", "via", "/net", "address", "73"]


_CRED_DRIVER = textwrap.dedent("""\
    import shal

    @shal.register
    class Svc(shal.Driver):
        compatible = "test,routes-cred"
        kind = shal.MessageTransport
    """)

_CRED_TOPO = textwrap.dedent("""\
    shal_version: 1
    root:
      svc:
        driver: shal,sim-msg
        address: sim0
        children:
          api:
            id: api
            driver: test,routes-cred
            address: "${SHAL_TEST_SVC_URL}"
            routes:
              - {via: /cloud, address: "${SHAL_TEST_SVC_URL}", name: cloud}
      cloud: {driver: "shal,sim-msg", address: sim1}
    """)


def test_routes_never_prints_a_credential_from_an_env_address(tmp_path, monkeypatch):
    # route addresses are ${ENV}-resolved: userinfo and a query token must not
    # reach stdout, in JSON or text (redact_url, #20)
    (tmp_path / "c.yaml").write_text(_CRED_TOPO, encoding="utf-8")
    (tmp_path / "drv.py").write_text(_CRED_DRIVER, encoding="utf-8")
    monkeypatch.setenv("SHAL_TEST_SVC_URL",
                       "https://bob:hunter2@api.example.com:8443/v1?token=s3cret")
    env = {**os.environ, "PYTHONPATH": _ENV["PYTHONPATH"]}
    r = _shal("routes", "c.yaml", "api", "--drivers", "drv.py", "--json",
              cwd=tmp_path, env=env)
    assert r.returncode == 0, r.stderr
    assert [x["address"] for x in json.loads(r.stdout)["routes"]] == [
        "https://api.example.com:8443/v1"] * 2
    t = _shal("routes", "c.yaml", "api", "--drivers", "drv.py", cwd=tmp_path, env=env)
    assert t.returncode == 0, t.stderr
    assert "address https://api.example.com:8443/v1" in t.stdout
    for out in (r.stdout, t.stdout):
        for secret in ("bob", "hunter2", "token", "s3cret"):
            assert secret not in out


def test_routes_unknown_device_is_exit_1_and_lists_the_devices(lab):
    r = _shal("routes", "t.yaml", "nope", "--json", cwd=lab)
    assert r.returncode == 1
    out = json.loads(r.stdout)
    assert out["ok"] is False and "no device 'nope'" in out["error"]
    assert "board" in out["error"] and out["error"] in r.stderr


def test_routes_help_shows_the_json_shape(lab):
    r = _shal("routes", "--help", cwd=lab)
    assert r.returncode == 0
    assert '"routes": [{"name": "console", "via": "/console"' in r.stdout


# ---- the agent path (RFC-001 "Agent path"), --json only ---------------------------

def test_agent_path_tools_then_call_via_then_routes_reading_only_json(lab):
    # 1. see the routed node once, with its routes
    r = _shal("tools", "t.yaml", "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    routed = [t for t in json.loads(r.stdout)["tools"] if "routes" in t]
    assert {t["device"] for t in routed} == {"board"}
    tool = next(t for t in routed if t["kind"] == "read")
    device, op, names = tool["device"], tool["op"], tool["routes"]
    assert names == ["console", "ssh"]
    assert tool["input_schema"]["properties"]["via"]["enum"] == names

    # 2. a bad route name: the error lists the valid names, nothing was sent
    r = _shal("call", "t.yaml", device, op, "--via", "wifi", "--json", cwd=lab)
    assert r.returncode == 3
    bad = json.loads(r.stdout)
    assert bad["ok"] is False and bad["routes"] == names

    # 3. follow the error to a valid name and pin it
    r = _shal("call", "t.yaml", device, op, "--via", bad["routes"][-1], "--json",
              cwd=lab)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["ok"] is True and out["via"] == "ssh"
    assert isinstance(out["result"], float)

    # 4. read the declaration: the same names, in the same order
    r = _shal("routes", "t.yaml", device, "--json", cwd=lab)
    assert r.returncode == 0, r.stderr
    assert [x["name"] for x in json.loads(r.stdout)["routes"]] == names
