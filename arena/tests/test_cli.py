"""`shal-arena` CLI, invoked as a subprocess like an agent would (issue #310
DoD: "A test runs the CLI with no prompt and parses the `--json` output from
a sample task"). `stdin=DEVNULL` + a timeout make a hidden prompt a test
failure, not a hang."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from shal_arena.loader import load_task
from shal_arena.runner import pick_fault
from shal_arena.store import RunStore

from .conftest import PASSING_DRIVER, SAMPLE_TASK

_TIMEOUT = 30


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TIMEOUT)


def test_run_cli_no_prompt_parses_json_from_sample_task(tmp_path: Path) -> None:
    # --state-dir under tmp_path: a bare `run` with no --state-dir would default to
    # ./.shal-arena, writing into whatever directory the test happens to run from.
    proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(tmp_path / "state"), "--json")
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    assert doc["run_id"]
    assert {i["address"] for i in doc["instruments"]} == {"psu0", "dmm0"}


def test_bad_task_file_exits_nonzero_with_fix_in_message(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("arena_task: 1\nid: bad\n", encoding="utf-8")
    proc = _run_cli("run", str(bad), "--state-dir", str(tmp_path / "state"), "--json")
    assert proc.returncode != 0
    doc = json.loads(proc.stdout)
    assert doc["ok"] is False
    assert doc["error"]["fix"]
    assert doc["error"]["fix"] in proc.stdout  # --fix is self-contained in the JSON too


def test_run_check_answer_roundtrip_via_cli(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"

    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    check_proc = _run_cli("check", run_id, "psu0", str(PASSING_DRIVER),
                          "--state-dir", str(state_dir), "--json")
    assert check_proc.returncode == 0, check_proc.stderr
    assert json.loads(check_proc.stdout)["passed"] is True

    # recomputed the same way `answer` does — no secret file exists to peek at
    # (CTO review on #319): the fault is derived from the run's own stored seed.
    run_state = RunStore(state_dir).load(run_id)
    fault_id = pick_fault(load_task(SAMPLE_TASK).card, run_state.seed)
    answer_proc = _run_cli("answer", run_id, fault_id,
                           "--state-dir", str(state_dir), "--json")
    assert answer_proc.returncode == 0, answer_proc.stderr
    assert json.loads(answer_proc.stdout)["correct"] is True


def test_check_reports_exit_1_when_driver_has_problems(tmp_path: Path) -> None:
    from .conftest import FAILING_DRIVER
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]
    check_proc = _run_cli("check", run_id, "psu0", str(FAILING_DRIVER),
                          "--state-dir", str(state_dir), "--json")
    assert check_proc.returncode == 1
    assert json.loads(check_proc.stdout)["passed"] is False
