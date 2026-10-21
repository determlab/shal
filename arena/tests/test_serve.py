"""issue #486: `shal-arena serve` -- a headless local server that owns the
seed, the simulator and the run state -- and `--server URL` on the run
commands. The server runs in its own process (as a harness would run it);
the client is the real CLI, in this process, in an empty working directory.
"""
from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from shal_arena import fault as _fault
from shal_arena.cli import main
from shal_arena.loader import load_task, resolve_task
from shal_arena.remote import MAX_PAYLOAD_BYTES
from shal_arena.store import RunStore

from .conftest import (
    PASSING_DMM_DRIVER,
    PASSING_RELAY_DRIVER,
    PASSING_TEMP_DRIVER,
)

_ARENA_SRC = Path(__file__).resolve().parents[1] / "src"
_TASK = "relay-rail"


def _start_server(state_dir: Path, *extra: str, env: dict | None = None,
                  stderr=subprocess.DEVNULL) -> tuple[subprocess.Popen, dict]:
    full_env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(_ARENA_SRC), os.environ.get("PYTHONPATH", "")]), **(env or {})}
    proc = subprocess.Popen(
        [sys.executable, "-u", "-m", "shal_arena.cli", "serve", "--json",
         "--state-dir", str(state_dir), *extra],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, text=True,
        env=full_env)
    line = proc.stdout.readline()
    if not line:
        proc.wait(timeout=10)
        raise RuntimeError(f"serve exited early with {proc.returncode}")
    return proc, json.loads(line)


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    proc.stdout.close()


@pytest.fixture
def server(tmp_path: Path):
    state_dir = tmp_path / "server-state"
    proc, doc = _start_server(state_dir)
    try:
        yield doc["url"], state_dir, proc
    finally:
        _stop(proc)


@pytest.fixture
def client_dir(tmp_path: Path, monkeypatch) -> Path:
    """The agent's own working directory: empty, and the cwd of every client
    call. Nothing may ever appear in it."""
    path = tmp_path / "client"
    path.mkdir()
    monkeypatch.chdir(path)
    return path


def _cli(capsys, *args: str) -> tuple[int, dict]:
    capsys.readouterr()
    code = main([*args, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out)


def _post(url: str, path: str, body: dict, headers: dict | None = None) -> tuple[int, dict]:
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _start(capsys, url: str, seed: int = 1) -> tuple[str, str]:
    code, doc = _cli(capsys, "run", _TASK, "--server", url, "--seed", str(seed))
    assert code == 0, doc
    return doc["run_id"], doc["token"]


def _realized(seed: int) -> str:
    loaded = load_task(resolve_task(_TASK))
    return _fault.realized_fault(loaded.card, seed).fault_id


def _seed_with(*faults: str) -> int:
    return next(s for s in range(200) if _realized(s) in faults)


def _simlog(state_dir: Path, run_id: str) -> list[dict]:
    path = RunStore(state_dir).sim_log_path(run_id)
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# the same fields as a local run; the client keeps nothing; no seed or fault
# --------------------------------------------------------------------------- #

def test_a_served_run_returns_the_same_fields_as_a_local_one(server, client_dir, tmp_path, capsys):
    url, state_dir, _proc = server
    seed = _seed_with("low_voltage", "overheat")
    local_dir = str(tmp_path / "local-state")

    def local(*args: str) -> dict:
        return _cli(capsys, *args, "--state-dir", local_dir)[1]

    l_run = local("run", _TASK, "--seed", str(seed))
    r_code, r_run = _cli(capsys, "run", _TASK, "--seed", str(seed), "--server", url)
    assert r_code == 0
    assert set(r_run) == set(l_run) | {"token"}
    run_id, token = r_run["run_id"], r_run["token"]
    srv = ("--server", url, "--token", token)

    steps = [
        (("measure", "{run}", "dmm0", str(PASSING_DMM_DRIVER)), ()),
        (("measure", "{run}", "temp0", str(PASSING_TEMP_DRIVER)), ()),
        (("drive", "{run}", "psu0", "12.0"), ()),
        (("call", "{run}", "relay0", str(PASSING_RELAY_DRIVER), "set_relay", "0", "true"), ()),
        (("call", "{run}", "relay0", str(PASSING_RELAY_DRIVER), "read_relay", "0"), ()),
    ]
    responses = []
    for args, _ in steps:
        l_doc = local(*[a.format(run=l_run["run_id"]) for a in args])
        code, r_doc = _cli(capsys, *[a.format(run=run_id) for a in args], *srv)
        assert code == 0, r_doc
        assert set(r_doc) == set(l_doc), args[0]
        assert r_doc["side_effect"] == l_doc["side_effect"]
        responses.append(r_doc)

    l_ans = local("answer", l_run["run_id"], "ok")
    code, r_ans = _cli(capsys, "answer", run_id, "ok", *srv)
    assert code == 0
    responses.append(r_ans)
    assert set(r_ans) <= set(l_ans)
    assert set(l_ans) - set(r_ans) <= {"fault_id", "task_path", "card_path", "sim_log"}
    assert set(l_ans["score"]) - set(r_ans["score"]) <= {
        "seed", "fault_type", "faults_total", "faults_caught", "false_fails",
        "error_fail_correct"}
    assert r_ans["correct"] == l_ans["correct"] and r_ans["disqualified"] is False

    # no response carries the seed key, the fault's name, or a real path
    for doc in [r_run, *responses]:
        text = json.dumps(doc)
        assert '"seed"' not in text and '"fault_type"' not in text
    fault = _realized(seed)
    for doc in responses:
        assert f'"{fault}"' not in json.dumps(doc)
    assert "fault_id" not in r_ans and str(state_dir) not in json.dumps(r_ans)

    # the client wrote nothing: no state dir, no seed, no cli.jsonl
    assert list(client_dir.iterdir()) == []


# --------------------------------------------------------------------------- #
# the token
# --------------------------------------------------------------------------- #

def test_a_call_without_the_right_token_is_403_naming_the_fix(server, client_dir, capsys):
    url, _state_dir, _proc = server
    run_a, token_a = _start(capsys, url)
    run_b, _token_b = _start(capsys, url)

    for token in (None, "wrong", token_a + "x"):
        extra = [] if token is None else ["--token", token]
        code, doc = _cli(capsys, "measure", run_a, "dmm0", str(PASSING_DMM_DRIVER),
                         "--server", url, *extra)
        assert code == 2 and doc["ok"] is False
        assert "pass the token that `run` returned" in doc["error"]["fix"]

    # run A's token is refused for run B (and the reverse)
    code, doc = _cli(capsys, "answer", run_b, "ok", "--server", url, "--token", token_a)
    assert code == 2 and "token" in doc["error"]["fix"]
    code, _doc = _cli(capsys, "answer", run_a, "ok", "--server", url, "--token", token_a)
    assert code == 0

    status, body = _post(url, "/api/play/drive", {"run_id": run_b, "address": "psu0", "volts": 5})
    assert status == 403 and "pass the token that `run` returned" in body["error"]["fix"]
    status, _ = _post(url, "/api/play/drive", {"run_id": "no-such-run", "token": token_a,
                                               "address": "psu0", "volts": 5})
    assert status == 403


# --------------------------------------------------------------------------- #
# the same checks as the Play routes; localhost by default
# --------------------------------------------------------------------------- #

def test_serve_binds_127_0_0_1_and_refuses_a_wrong_host_origin_or_content_type(server):
    url, _state_dir, _proc = server
    assert url.startswith("http://127.0.0.1:")
    port = int(url.rsplit(":", 1)[1])
    body = json.dumps({"task": _TASK}).encode()

    def raw(host: str | None, headers: dict) -> int:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        conn.putrequest("POST", "/api/play/start", skip_host=True)
        if host is not None:
            conn.putheader("Host", host)
        for k, v in headers.items():
            conn.putheader(k, v)
        conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        status = conn.getresponse().status
        conn.close()
        return status

    ok = {"Content-Type": "application/json"}
    assert raw(f"127.0.0.1:{port}", ok) == 200
    assert raw("evil.example:80", ok) == 403
    assert raw(f"127.0.0.1:{port}", {**ok, "Origin": "http://evil.example"}) == 403
    assert raw(f"127.0.0.1:{port}", {"Content-Type": "text/plain"}) == 403
    status, _ = _post(url, "/api/play/start", {"task": _TASK},
                      {"Origin": f"http://127.0.0.1:{port}"})
    assert status == 200
    # headless: nothing is served on GET
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        urllib.request.urlopen(url + "/", timeout=10)
    assert excinfo.value.code == 404


def test_another_host_needs_an_explicit_flag_and_prints_a_warning(tmp_path):
    try:
        proc, doc = _start_server(tmp_path / "s", "--host", "127.0.0.2", stderr=subprocess.PIPE)
    except RuntimeError:
        pytest.skip("127.0.0.2 cannot be bound here")
    try:
        assert doc["url"].startswith("http://127.0.0.2:")
        assert doc["side_effect"] == "starts a local server; writes run state under --state-dir"
        status, _ = _post(doc["url"], "/api/play/start", {"task": _TASK})
        assert status == 200
    finally:
        proc.terminate()
        _out, err = proc.communicate(timeout=10)
    assert "WARNING" in err and "127.0.0.2" in err


# --------------------------------------------------------------------------- #
# the server's own state: cli.jsonl, record, export
# --------------------------------------------------------------------------- #

def test_the_server_writes_the_run_files_and_export_works_from_them(server, client_dir, capsys):
    url, state_dir, _proc = server
    run_id, token = _start(capsys, url)
    srv = ("--server", url, "--token", token)
    assert _cli(capsys, "measure", run_id, "dmm0", str(PASSING_DMM_DRIVER), *srv,
                "--note", "reading the rail")[0] == 0
    assert _cli(capsys, "answer", run_id, "ok", *srv)[0] == 0

    store = RunStore(state_dir)
    rows = [json.loads(ln) for ln in
            store.cli_log_path(run_id).read_text(encoding="utf-8").splitlines()]
    assert [r["argv"][0] for r in rows] == ["run", "measure", "answer"]
    assert rows[1]["note"] == "reading the rail"
    assert token not in store.cli_log_path(run_id).read_text(encoding="utf-8")
    assert store.record_path(run_id).is_file() and store.score_path(run_id).is_file()

    out = client_dir.parent / "export.html"
    assert main(["ui", "--export", str(out), "--run", run_id,
                 "--state-dir", str(state_dir)]) == 0
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html") \
        or "<html" in out.read_text(encoding="utf-8")
    assert list(client_dir.iterdir()) == []


# --------------------------------------------------------------------------- #
# the remote bus: the driver runs on the client, the bus calls go to the server
# --------------------------------------------------------------------------- #

def test_driver_bus_calls_reach_the_server_as_exchange_and_txn(server, client_dir, capsys):
    url, state_dir, _proc = server
    run_id, token = _start(capsys, url)
    srv = ("--server", url, "--token", token)
    assert _cli(capsys, "call", run_id, "relay0", str(PASSING_RELAY_DRIVER),
                "set_relay", "0", "true", *srv)[0] == 0
    assert _cli(capsys, "measure", run_id, "dmm0", str(PASSING_DMM_DRIVER), *srv)[0] == 0
    assert _cli(capsys, "measure", run_id, "temp0", str(PASSING_TEMP_DRIVER), *srv)[0] == 0

    entries = _simlog(state_dir, run_id)
    modbus = [e for e in entries if e["address"] == "relay0" and e["kind"] == "exchange"]
    assert modbus and modbus[0]["request"] == {"fc": 5, "address": 0, "value": True}
    scpi = [e for e in entries if e["address"] == "dmm0" and e["kind"] == "query"]
    assert scpi and scpi[0]["cmd"] == "MEAS:VOLT:DC?"
    i2c = [e for e in entries if e["address"] == "temp0" and e["kind"] == "exchange"]
    assert i2c and i2c[0]["bus_family"] == "sim_i2c"


def test_the_wire_takes_messages_and_hex_bytes(server, client_dir, capsys):
    url, state_dir, _proc = server
    run_id, token = _start(capsys, url)
    auth = {"run_id": run_id, "token": token}

    _, dmm = _post(url, "/api/remote/begin", {**auth, "action": "measure", "address": "dmm0"})
    status, reply = _post(url, "/api/remote/exchange", {
        **auth, "session": dmm["session"], "address": dmm["child_address"],
        "message": {"scpi": "MEAS:VOLT:DC?", "query": True}})
    assert status == 200 and reply["ok"] and float(reply["reply"]["reply"]) > 0

    _, temp = _post(url, "/api/remote/begin", {**auth, "action": "measure", "address": "temp0"})
    status, reply = _post(url, "/api/remote/txn", {
        **auth, "session": temp["session"], "address": temp["child_address"],
        "ops": [{"op": "write", "hex": "00"}, {"op": "read", "n": 2}]})
    assert status == 200 and len(bytes.fromhex(reply["hex"])) == 2
    status, _ = _post(url, "/api/remote/txn", {
        **auth, "session": temp["session"], "address": temp["child_address"],
        "ops": [{"op": "write", "hex": "zz"}]})
    assert status == 400
    assert {e["address"] for e in _simlog(state_dir, run_id)} >= {"dmm0", "temp0"}


def test_a_remote_bus_payload_over_the_cap_is_413_and_needs_the_token(server, client_dir, capsys):
    url, _state_dir, _proc = server
    run_id, token = _start(capsys, url)
    _, dmm = _post(url, "/api/remote/begin", {"run_id": run_id, "token": token,
                                              "action": "measure", "address": "dmm0"})
    big = {"run_id": run_id, "token": token, "session": dmm["session"],
           "address": dmm["child_address"],
           "message": {"scpi": "X" * (MAX_PAYLOAD_BYTES + 10), "query": True}}
    status, body = _post(url, "/api/remote/exchange", big)
    assert status == 413 and str(MAX_PAYLOAD_BYTES) in body["error"]["message"]

    small = {"session": dmm["session"], "address": dmm["child_address"],
             "message": {"scpi": "MEAS:VOLT:DC?", "query": True}}
    status, body = _post(url, "/api/remote/exchange", {"run_id": run_id, **small})
    assert status == 403 and "pass the token that `run` returned" in body["error"]["fix"]
    status, _ = _post(url, "/api/remote/exchange", {"run_id": run_id, "token": token, **small})
    assert status == 200
    # the server is still in step after the drained 413
    status, _ = _post(url, "/api/remote/exchange", {"run_id": run_id, "token": token, **small})
    assert status == 200


# --------------------------------------------------------------------------- #
# no code on the server
# --------------------------------------------------------------------------- #

def test_no_route_takes_code_a_path_or_a_module_and_the_server_never_imports_a_driver(
        server, client_dir, capsys, tmp_path):
    url, _state_dir, _proc = server
    run_id, token = _start(capsys, url)
    auth = {"run_id": run_id, "token": token}

    for route, body in [
        ("/api/play/measure", {**auth, "address": "dmm0", "driver": str(PASSING_DMM_DRIVER)}),
        ("/api/remote/begin", {**auth, "action": "measure", "address": "dmm0",
                               "manifest": str(PASSING_DMM_DRIVER)}),
        ("/api/remote/begin", {**auth, "action": "measure", "address": "dmm0",
                               "module": "os"}),
        ("/api/remote/finish", {**auth, "session": "x", "code": "print(1)"}),
        ("/api/play/start", {"task": str(resolve_task(_TASK))}),
        ("/api/play/start", {"task": _TASK, "path": "/etc/passwd"}),
    ]:
        status, doc = _post(url, route, body)
        assert status == 400 and doc["ok"] is False and doc["error"]["fix"], route
    for route in ("/api/remote/run", "/api/remote/import", "/api/exec", "/api/demo/start"):
        status, _ = _post(url, route, auth)
        assert status == 404, route

    # a driver that records which process imports it: only this one may
    marker = tmp_path / "imports.txt"
    driver = tmp_path / "driver_dir" / "marked_dmm_driver.py"
    driver.parent.mkdir()
    future = "from __future__ import annotations\n"
    driver.write_text(
        PASSING_DMM_DRIVER.read_text(encoding="utf-8").replace(
            future, future + "import os\n"
            f"open({str(marker)!r}, 'a').write(str(os.getpid()) + '\\n')\n", 1),
        encoding="utf-8")
    assert _cli(capsys, "measure", run_id, "dmm0", str(driver), "--server", url,
                "--token", token)[0] == 0
    assert set(marker.read_text(encoding="utf-8").split()) == {str(os.getpid())}


# --------------------------------------------------------------------------- #
# scoring comes from the server's own traffic
# --------------------------------------------------------------------------- #

def test_an_answer_with_no_probe_traffic_is_disqualified_even_with_a_made_up_reading(
        server, client_dir, capsys):
    url, _state_dir, _proc = server

    run_a, token_a = _start(capsys, url)
    code, doc = _cli(capsys, "answer", run_a, "ok", "--server", url, "--token", token_a)
    assert code == 0 and doc["disqualified"] is True

    # a session opened and finished with a reading, but no bus request at all
    run_b, token_b = _start(capsys, url)
    auth = {"run_id": run_b, "token": token_b}
    _, sess = _post(url, "/api/remote/begin", {**auth, "action": "measure", "address": "dmm0"})
    status, _ = _post(url, "/api/remote/finish", {
        **auth, "session": sess["session"], "op": "measure_voltage", "reading": 3.3,
        "unit": "volt"})
    assert status == 200
    code, doc = _cli(capsys, "answer", run_b, "ok", "--server", url, "--token", token_b)
    assert doc["disqualified"] is True

    # real traffic to a probe instrument: scored on the answer only
    run_c, token_c = _start(capsys, url)
    assert _cli(capsys, "measure", run_c, "dmm0", str(PASSING_DMM_DRIVER),
                "--server", url, "--token", token_c)[0] == 0
    code, doc = _cli(capsys, "answer", run_c, "ok", "--server", url, "--token", token_c)
    assert doc["disqualified"] is False


# --------------------------------------------------------------------------- #
# check-driver never contacts the server
# --------------------------------------------------------------------------- #

def test_check_driver_with_server_uses_only_the_local_fault_free_case(
        tmp_path, client_dir, capsys):
    proc, doc = _start_server(tmp_path / "s")
    _stop(proc)  # the server is stopped; the check must not need it
    code, out = _cli(capsys, "check-driver", "run-never-started", "dmm",
                     str(PASSING_DMM_DRIVER), "--server", doc["url"])
    assert code == 0 and out["passed"] is True and out["problems"] == []
    assert list(client_dir.iterdir()) == []

    # the instrument may be an address; the driver says which case it is
    code, out = _cli(capsys, "check-driver", "run-never-started", "dmm0",
                     str(PASSING_DMM_DRIVER), "--server", doc["url"])
    assert code == 0 and out["case"] == "dmm"

    # and a measure against the stopped server is a clean, named error
    code, out = _cli(capsys, "measure", "run-x", "dmm0", str(PASSING_DMM_DRIVER),
                     "--server", doc["url"], "--token", "t")
    assert code == 1 and out["error"]["fix"]
