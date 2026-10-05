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


def test_drive_cli_shows_state_change_in_json_and_sim_log(tmp_path: Path) -> None:
    """issue #313 Agent path, CTO review on #323 and #330: an agent reaches
    `CardSim.state`/`apply_input` through the `shal-arena` CLI alone, no
    Python import. 6.5 V on `vin` (destroyed above 6.0 V) is a damage limit,
    so the SHAL-side gate (issue #330) refuses it, exit 0, nothing applied;
    the same voltage over `raw_scpi` (no gate) destroys the card instead,
    and that lands in the run's sim log."""
    from shal_arena.runner import raw_scpi

    state_dir = tmp_path / "state"

    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    # buck-5v-3v3.yaml: vin is destroyed above 6.0 V.
    drive_proc = _run_cli("drive", run_id, "psu0", "6.5", "--state-dir", str(state_dir), "--json")
    assert drive_proc.returncode == 0, drive_proc.stderr
    drive_doc = json.loads(drive_proc.stdout)
    assert drive_doc["ok"] is False
    assert drive_doc["sent"] is False
    assert drive_doc["rejected"] == "approval"
    assert drive_doc["side_effect"] == "write"
    assert drive_doc["state"] == "ok"
    assert drive_doc["fix"]

    sim_log_path = RunStore(state_dir).sim_log_path(run_id)
    lines = [json.loads(ln) for ln in sim_log_path.read_text(encoding="utf-8").splitlines()]
    assert any(ln["kind"] == "refused" and ln["address"] == "psu0" for ln in lines), lines
    assert not any(ln["kind"] == "damage" for ln in lines), lines

    # the destroy path: raw_scpi has no gate (issue #314/#330).
    raw_scpi(run_id, "psu0", "VOLT 6.5", state_dir=state_dir)
    lines = [json.loads(ln) for ln in sim_log_path.read_text(encoding="utf-8").splitlines()]
    assert any(ln["kind"] == "damage" and ln["address"] == "psu0" for ln in lines), lines


def test_refused_input_on_closed_run_exits_nonzero_with_fix_in_json(
        tmp_path: Path, capsys) -> None:
    from shal_arena.cli import main

    state_dir = str(tmp_path / "state")
    assert main(["run", str(SAMPLE_TASK), "--state-dir", state_dir, "--json"]) == 0
    run_id = json.loads(capsys.readouterr().out)["run_id"]
    assert main(["answer", run_id, "ok", "--state-dir", state_dir, "--json"]) == 0
    capsys.readouterr()
    code = main(["drive", run_id, "psu0", "5.0", "--state-dir", state_dir, "--json"])
    assert code != 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["ok"] is False
    assert set(doc["error"]) == {"type", "message", "fix"}
    assert "closed" in doc["error"]["message"]
    assert "shal-arena run" in doc["error"]["fix"]


def test_check_reports_exit_1_when_driver_has_problems(tmp_path: Path) -> None:
    from .conftest import FAILING_DRIVER
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]
    check_proc = _run_cli("check", run_id, "psu0", str(FAILING_DRIVER),
                          "--state-dir", str(state_dir), "--json")
    assert check_proc.returncode == 1
    assert json.loads(check_proc.stdout)["passed"] is False
