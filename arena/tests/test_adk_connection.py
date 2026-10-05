"""The ADK connection (issue #311): the packaged `scpi-psu` and `dmm` ADK
cases (`shal_arena/adk/`) wired to the runner's tile state, and the
`shal-arena check-driver` Agent path. DoD:
- `pytest arena/tests/test_adk_connection.py -q` passes for both instruments.
- A known-good manifest passes; a broken one fails with a message naming the
  fix (on the real `shal.conformance.check_driver` output — no mocking).
- The `--json` output is parsed in the test the way an agent would.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena.runner import check_instrument_driver, start_run

from .conftest import (
    FAILING_DMM_DRIVER,
    FAILING_DRIVER,
    PASSING_DMM_DRIVER,
    PASSING_DRIVER,
    SAMPLE_TASK,
)

_TIMEOUT = 30

# Each packaged ADK case: its run address and its known-good / known-bad manifest.
_INSTRUMENTS = [
    pytest.param("psu0", PASSING_DRIVER, FAILING_DRIVER, id="scpi-psu"),
    pytest.param("dmm0", PASSING_DMM_DRIVER, FAILING_DMM_DRIVER, id="dmm"),
]


def _run_cli(*args: str) -> subprocess.CompletedProcess:
    # stdin=DEVNULL + a timeout: a hidden prompt is a test failure, not a hang,
    # same as test_cli.py's rule for the rest of the arena CLI.
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=_TIMEOUT)


@pytest.mark.parametrize("address, passing_driver, failing_driver", _INSTRUMENTS)
def test_known_good_manifest_lights_the_tile(
        address: str, passing_driver: Path, failing_driver: Path, tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]

    check = check_instrument_driver(run_id, address, passing_driver, state_dir=tmp_path)

    assert check["passed"] is True
    assert check["problems"] == []


@pytest.mark.parametrize("address, passing_driver, failing_driver", _INSTRUMENTS)
def test_broken_manifest_fails_with_the_fix_and_does_not_light_the_tile(
        address: str, passing_driver: Path, failing_driver: Path, tmp_path: Path) -> None:
    result = start_run(SAMPLE_TASK, state_dir=tmp_path)
    run_id = result["run_id"]

    check = check_instrument_driver(run_id, address, failing_driver, state_dir=tmp_path)

    assert check["passed"] is False
    assert check["problems"]
    # the real shal.conformance.check_driver problem, not a stand-in: it names
    # the missing `llm_ready` flag the broken fixture leaves unset.
    assert any("llm_ready" in p for p in check["problems"])


@pytest.mark.parametrize("address, passing_driver, failing_driver", _INSTRUMENTS)
def test_check_driver_cli_json_round_trip(
        address: str, passing_driver: Path, failing_driver: Path, tmp_path: Path) -> None:
    """Parses `--json` the way an agent would: run the CLI as a subprocess,
    `json.loads` stdout, read `passed`/`problems` off the document — never a
    peek at the in-process Python objects."""
    state_dir = tmp_path / "state"

    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    assert run_proc.returncode == 0, run_proc.stderr
    run_id = json.loads(run_proc.stdout)["run_id"]

    good_proc = _run_cli("check-driver", run_id, address, str(passing_driver),
                         "--state-dir", str(state_dir), "--json")
    assert good_proc.returncode == 0, good_proc.stderr
    good_doc = json.loads(good_proc.stdout)
    assert good_doc["ok"] is True
    assert good_doc["passed"] is True

    bad_proc = _run_cli("check-driver", run_id, address, str(failing_driver),
                        "--state-dir", str(state_dir), "--json")
    assert bad_proc.returncode == 1
    bad_doc = json.loads(bad_proc.stdout)
    assert bad_doc["passed"] is False
    assert bad_doc["problems"]


def test_check_alias_still_works_for_the_same_command(tmp_path: Path) -> None:
    """`check` (issue #310's original name) stays a working alias of
    `check-driver` (issue #311's Agent path name) — existing callers don't
    break when this ticket renames the command."""
    state_dir = tmp_path / "state"
    run_proc = _run_cli("run", str(SAMPLE_TASK), "--state-dir", str(state_dir), "--json")
    run_id = json.loads(run_proc.stdout)["run_id"]

    proc = _run_cli("check", run_id, "psu0", str(PASSING_DRIVER),
                    "--state-dir", str(state_dir), "--json")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["passed"] is True
