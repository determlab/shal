"""issue #416: `shal-arena run` takes a packaged task name (package data, not a
repo path), `shal-arena tasks --json` lists them, and an unknown name errors
with the valid names and the command that lists them."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .conftest import SAMPLE_TASK

_NAMES = ["easy", "medium", "hard", "rail-3v3"]


def _cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    # arena/src on PYTHONPATH so this also runs from a bare clone (conftest.py's
    # own sys.path tweak does not reach a subprocess).
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        [str(SAMPLE_TASK.parents[2]), os.environ.get("PYTHONPATH", "")])}
    return subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", *args],
        cwd=cwd, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)


def test_tasks_json_lists_every_packaged_task(tmp_path: Path) -> None:
    proc = _cli(["tasks", "--json"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    assert {t["name"] for t in doc["tasks"]} == set(_NAMES)
    for t in doc["tasks"]:
        assert set(t) == {"name", "path", "level"}
        assert Path(t["path"]).is_file()
        assert t["level"] in {"easy", "medium", "hard"}


def test_run_resolves_every_packaged_name(tmp_path: Path) -> None:
    for name in _NAMES:
        proc = _cli(["run", name, "--state-dir", str(tmp_path / name), "--json"], tmp_path)
        assert proc.returncode == 0, f"{name}: {proc.stderr}"
        doc = json.loads(proc.stdout)
        assert doc["ok"] is True
        assert doc["task"]["id"] == name


def test_run_still_takes_a_file_path(tmp_path: Path) -> None:
    proc = _cli(["run", str(SAMPLE_TASK), "--json"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["task"]["id"] == "rail-3v3"


def test_unknown_name_lists_valid_names_and_the_tasks_command(tmp_path: Path) -> None:
    proc = _cli(["run", "nope", "--json"], tmp_path)
    assert proc.returncode != 0
    err = json.loads(proc.stdout)["error"]
    for name in _NAMES:
        assert name in err["message"]
    assert "shal-arena tasks --json" in err["fix"]
