"""issue #449 (follow-up to #436/#442 CTO review): past the retry deadline,
`store.py`'s `_retry_on_permission_error` must raise `FileBusy` -- naming the
file and "close it and retry" -- not let the raw `PermissionError` escape to
a traceback with no fix."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from shal_arena import store as store_mod
from shal_arena.errors import FileBusy
from shal_arena.runner import start_run
from shal_arena.store import RunStore

from .conftest import SAMPLE_TASK


def test_retry_raises_file_busy_naming_the_file_and_the_fix(tmp_path: Path,
                                                             monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_IO_RETRY_TIMEOUT_S", 0.05)
    target = tmp_path / "some-run.json"

    def always_denied():
        raise PermissionError(5, "Access is denied")

    with pytest.raises(FileBusy) as ei:
        store_mod._retry_on_permission_error(always_denied, path=target)
    assert str(target) in str(ei.value)
    assert "close it and retry" in str(ei.value)
    assert isinstance(ei.value.__cause__, PermissionError)


def test_a_locked_load_raises_file_busy_not_a_raw_permission_error(
        tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store_mod, "_IO_RETRY_TIMEOUT_S", 0.05)
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]

    def always_denied(*a, **kw):
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(Path, "read_text", lambda self, **kw: always_denied())
    store = RunStore(tmp_path)
    with pytest.raises(FileBusy) as ei:
        store.load(run_id)
    assert run_id in str(ei.value)
    assert "close it and retry" in str(ei.value)


_CLI_SCRIPT = """\
import sys
from shal_arena import store as store_mod
store_mod._IO_RETRY_TIMEOUT_S = 0.05

_real_replace = __import__("os").replace
calls = {{"n": 0}}

def _denied_once_then_real(src, dst):
    calls["n"] += 1
    raise PermissionError(5, "Access is denied")

import os
os.replace = _denied_once_then_real

from shal_arena.cli import main
sys.exit(main(["answer", {run_id!r}, "ok", "--state-dir", {state_dir!r}, "--json"]))
"""


def test_cli_exits_nonzero_with_the_fix_and_no_traceback_when_the_run_file_stays_busy(
        tmp_path: Path) -> None:
    """Done-when: "run as a CLI subprocess in that state, the command exits
    non-zero with that one line and no traceback." `os.replace` is patched
    to always deny -- the same observable effect as another program holding
    the run file open past this run's own retry deadline."""
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    proc = subprocess.run(
        [sys.executable, "-c", _CLI_SCRIPT.format(run_id=run_id, state_dir=str(tmp_path))],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode != 0
    assert "Traceback" not in proc.stderr
    assert "close it and retry" in proc.stderr
    doc = proc.stdout.strip()
    assert '"ok": false' in doc or '"ok":false' in doc
