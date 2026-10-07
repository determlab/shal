"""issue #460: every `shal-arena` CLI call this run served is logged to
``<run>.cli.jsonl`` -- the demo page's agent-terminal window (#447) reads
the real commands and answers for any agent from this one file, not from
anything vendor-specific. Each line is ``{time, argv, exit_code, json}``
(``text``/``json: null`` instead when the command printed no JSON)."""
from __future__ import annotations

import concurrent.futures
import json
import queue
import subprocess
import sys
import threading
from pathlib import Path

from shal_arena.cli import _redact_cli_line
from shal_arena.store import RunStore

from .conftest import PASSING_DMM_DRIVER, SAMPLE_TASK


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)


def _lines(state_dir: Path, run_id: str) -> list[dict]:
    path = RunStore(state_dir).cli_log_path(run_id)
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines()]


# --------------------------------------------------------------------------- #
# the one-place redaction helper -- `_redact_cli_line` is the exact function
# `_log_cli_call` runs every line through before it reaches disk.
# --------------------------------------------------------------------------- #

def test_a_secret_url_in_the_payload_appears_only_in_redacted_form() -> None:
    payload = {"endpoint": "https://user:s3cr3t-token@example.invalid/path?token=abc123"}
    _argv, redacted, _text = _redact_cli_line([], payload, None)
    dumped = json.dumps(redacted)
    assert "s3cr3t-token" not in dumped
    assert "token=abc123" not in dumped
    assert redacted["endpoint"] == "https://example.invalid/path"


def test_token_flag_with_a_space_is_redacted() -> None:
    argv, _payload, _text = _redact_cli_line(["--token", "X"], None, None)
    assert argv == ["--token", "***"]


def test_password_flag_with_equals_is_redacted() -> None:
    argv, _payload, _text = _redact_cli_line(["--password=X"], None, None)
    assert argv == ["--password=***"]


def test_a_nested_auth_key_is_masked_in_place() -> None:
    _argv, redacted, _text = _redact_cli_line([], {"auth": {"key": "shh"}}, None)
    assert redacted == {"auth": "***"}


def test_normal_values_stay_readable() -> None:
    argv, payload, _text = _redact_cli_line(["--seed", "9"], {"reading": 3.30}, None)
    assert argv == ["--seed", "9"]
    assert payload == {"reading": 3.30}


# --------------------------------------------------------------------------- #
# end-to-end: real CLI subprocesses, the run's own `<run>.cli.jsonl`.
# --------------------------------------------------------------------------- #

def test_three_commands_on_one_run_write_three_lines(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    measure_proc = _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                            "--state-dir", str(state_dir), "--json")
    assert measure_proc.returncode == 0, measure_proc.stderr

    answer_proc = _run_cli("answer", run_id, "ok", "--state-dir", str(state_dir), "--json")
    assert answer_proc.returncode == 0, answer_proc.stderr

    lines = _lines(state_dir, run_id)
    assert len(lines) == 3
    printed = [json.loads(p.stdout) for p in (run_proc, measure_proc, answer_proc)]
    for entry, proc, doc in zip(lines, (run_proc, measure_proc, answer_proc), printed,
                                strict=True):
        assert set(entry) == {"time", "argv", "exit_code", "json", "text"}
        assert entry["exit_code"] == proc.returncode
        assert entry["json"] == doc


def test_a_failing_command_logs_its_nonzero_exit_code_and_error_json(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]

    bad_manifest = tmp_path / "no_such_driver.py"
    fail_proc = _run_cli("check", run_id, "dmm0", str(bad_manifest),
                         "--state-dir", str(state_dir), "--json")
    assert fail_proc.returncode != 0

    lines = _lines(state_dir, run_id)
    failing = lines[-1]
    assert failing["exit_code"] == fail_proc.returncode != 0
    assert failing["json"]["ok"] is False
    assert failing["json"]["error"]


def test_eight_parallel_calls_on_one_run_give_eight_whole_valid_lines(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]

    def _measure_once(_i: int) -> subprocess.CompletedProcess:
        return _run_cli("measure", run_id, "dmm0", str(PASSING_DMM_DRIVER),
                        "--state-dir", str(state_dir), "--json")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        procs = list(pool.map(_measure_once, range(8)))
    assert all(p.returncode == 0 for p in procs), [p.stderr for p in procs]

    path = RunStore(state_dir).cli_log_path(run_id)
    raw_lines = path.read_text(encoding="utf-8").splitlines()
    measure_lines = [ln for ln in raw_lines if '"measure"' in ln]
    assert len(measure_lines) == 8
    for ln in raw_lines:
        json.loads(ln)  # each line is whole JSON on its own -- never torn or merged


def test_a_command_without_json_writes_redacted_text_with_json_null(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir))
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = run_proc.stdout.split()[1].rstrip(":")

    lines = _lines(state_dir, run_id)
    assert len(lines) == 1
    assert lines[0]["json"] is None
    assert lines[0]["text"] == run_proc.stdout


def test_tasks_and_help_write_nothing_run_writes_one_line(tmp_path: Path) -> None:
    tasks_proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "tasks", "--json"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert tasks_proc.returncode == 0, tasks_proc.stderr

    help_proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "--help"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    assert help_proc.returncode == 0, help_proc.stderr

    assert list(tmp_path.glob("*.cli.jsonl")) == []  # `tasks`/`--help` name no run

    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]
    assert len(_lines(state_dir, run_id)) == 1


def test_ui_server_stdout_is_not_held_back(tmp_path: Path) -> None:
    """#460 round 2 must-fix: `main()` used to buffer a WHOLE command's
    stdout and replay it only after the command returned, so `ui`'s own
    `watching <run> at <url>` line -- printed once, before it serves
    forever -- never reached anyone while `--no-open` kept a live server
    running under `--port 0` (a fresh, otherwise-unknown port an agent can
    only learn from that line)."""
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    proc = subprocess.Popen(
        [sys.executable, "-m", "shal_arena.cli", "ui", "--run", run_id,
         "--no-open", "--port", "0", "--state-dir", str(state_dir)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        q: queue.Queue[str] = queue.Queue()
        threading.Thread(target=lambda: q.put(proc.stdout.readline()), daemon=True).start()
        try:
            line = q.get(timeout=10)
        except queue.Empty:
            raise AssertionError("the 'watching ...' line never arrived within 10s") from None
        assert "watching" in line and run_id in line
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def test_an_unknown_or_escaping_run_id_creates_no_cli_log(tmp_path: Path) -> None:
    """#460 round 2 must-fix: the run id comes straight from argv/a reply,
    unchecked -- `answer no-such-run ok` used to leave an orphan
    `no-such-run.cli.jsonl` (and lock file), and a run id containing a path
    separator could write `<run>.cli.jsonl` outside the state dir
    entirely."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    unknown_proc = _run_cli("answer", "no-such-run", "ok",
                            "--state-dir", str(state_dir), "--json")
    assert unknown_proc.returncode != 0
    assert list(state_dir.glob("*.cli.jsonl")) == []

    escape_proc = _run_cli("answer", "../escaped", "ok",
                           "--state-dir", str(state_dir), "--json")
    assert escape_proc.returncode != 0
    assert list(state_dir.glob("*.cli.jsonl")) == []
    assert list(tmp_path.glob("*.cli.jsonl")) == []  # never escaped to the parent either


def test_a_userinfo_leak_in_argv_and_json_never_reaches_the_cli_log(tmp_path: Path) -> None:
    """CMO question on #460, answered by the CTO: this PR's redaction goes
    through #459's `redact_url_in_text`/`redact_structured`, so it
    inherits #459's userinfo fix. `answer <run> <value>` puts `value` in
    both `argv` and the printed `--json` (as `given`), so it's a real
    carrier for a secret url -- no `.cli.jsonl` line may ever show the
    userinfo."""
    state_dir = tmp_path / "state"
    # the userinfo itself, not the bare word "admin": a CI runner's own
    # username can legitimately contain "admin" (e.g. Windows CI's
    # "runneradmin", which appears in --state-dir's own path value) --
    # checking the full "user:pass@" substring avoids that false positive
    # while still proving the actual secret never reaches the log.
    leaks = [("ftp://admin:1234,5678@host", "admin:1234,5678@"),
            ("http://admin:9999;x@h", "admin:9999;x@")]
    for leak, userinfo in leaks:
        run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
        run_id = json.loads(run_proc.stdout)["run_id"]
        proc = _run_cli("answer", run_id, leak, "--state-dir", str(state_dir), "--json")
        assert proc.returncode == 0, proc.stderr

        raw = RunStore(state_dir).cli_log_path(run_id).read_text(encoding="utf-8")
        assert userinfo not in raw, (userinfo, raw)
