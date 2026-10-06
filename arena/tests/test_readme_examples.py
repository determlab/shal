"""issue #396: the two minimal driver examples named in arena/README.md's
"Write your driver" section run exactly as the README shows -- checked, then
played for a real reading -- so a cold agent copying them gets working code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest
from shal import registry

from shal_arena.runner import check_instrument_driver, start_run, take_measurement

from .conftest import RELAY_RAIL_TASK, SAMPLE_TASK

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
MINIMAL_PSU_DRIVER = _EXAMPLES_DIR / "minimal_psu_driver.py"
MINIMAL_DMM_DRIVER = _EXAMPLES_DIR / "minimal_dmm_driver.py"
MINIMAL_RELAY_DRIVER = _EXAMPLES_DIR / "minimal_relay_driver.py"
MINIMAL_TEMP_DRIVER = _EXAMPLES_DIR / "minimal_temp_driver.py"


@pytest.fixture(autouse=True)
def _clean_registry_slots():
    """None of the minimal examples use override=True (arena#394 made that
    unnecessary) -- start each test from a clean slot for every compatible,
    same reasoning as test_bench_driver_once.py's own fixture."""
    saved = {c: list(registry._entries.get(c, [])) for c in
             ("arena,bench-psu1", "arena,bench-dmm1",
              "arena,bench-relay1", "arena,bench-temp1")}
    for c in saved:
        registry._entries[c] = []
    try:
        yield
    finally:
        for c, candidates in saved.items():
            registry._entries[c] = candidates


def test_minimal_psu_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "psu0", MINIMAL_PSU_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_dmm_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "dmm0", MINIMAL_DMM_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_dmm_driver_example_takes_a_real_reading(tmp_path: Path) -> None:
    # A SEPARATE test (its own fresh registry slot, via the autouse fixture
    # above): calling check_instrument_driver and take_measurement for the
    # SAME instrument in the same process re-imports the SAME driver.py
    # twice outside of a bench run, which still "claims by 2 drivers" --
    # that general case is a separate bug from #394 (bench-loop specific)
    # and explicitly out of scope here ("Out of scope: bench code changes").
    # The README's own step 3 avoids it by checking one instrument and
    # measuring a DIFFERENT one, same as the existing "Agent path" section.
    run_id = start_run(str(SAMPLE_TASK), seed=1, state_dir=tmp_path)["run_id"]
    # seed 1's realized fault for rail-3v3.yaml's card is not `open` (README's
    # own walkthrough picks seed 1 for exactly this reason) -- so the probe
    # instrument is reachable, and this is a real reading, not just a check.
    result = take_measurement(run_id, "dmm0", MINIMAL_DMM_DRIVER, state_dir=tmp_path)
    assert isinstance(result["reading"], float)


def test_minimal_relay_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "relay0", MINIMAL_RELAY_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_temp_driver_example_passes_check(tmp_path: Path) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    report = check_instrument_driver(run_id, "temp0", MINIMAL_TEMP_DRIVER, state_dir=tmp_path)
    assert report["passed"] is True, report["problems"]


def test_minimal_temp_driver_example_takes_a_real_reading(tmp_path: Path) -> None:
    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    result = take_measurement(run_id, "temp0", MINIMAL_TEMP_DRIVER, state_dir=tmp_path)
    assert isinstance(result["reading"], float)


def test_minimal_relay_driver_example_calls_through_shal(tmp_path: Path) -> None:
    from shal_arena.runner import call_op

    run_id = start_run(str(RELAY_RAIL_TASK), seed=1, state_dir=tmp_path)["run_id"]
    result = call_op(run_id, "relay0", MINIMAL_RELAY_DRIVER, "read_relay", ["0"],
                     state_dir=tmp_path)
    assert result["ok"] is True
    assert result["result"] is True


def test_readme_examples_use_task_names_not_repo_paths() -> None:
    """issue #416: every README command that starts a run names the task, so
    it works after `pip install` with no checkout."""
    from .test_readme_commands import _readme_bash_commands

    for command in _readme_bash_commands():
        if command[:2] in (["shal-arena", "run"], ["shal-arena", "bench"]):
            assert not any(t.endswith(".yaml") for t in command), command


_WHEEL_ENV = "SHAL_ARENA_TEST_WHEEL"


@pytest.mark.skipif(os.environ.get(_WHEEL_ENV) != "1",
                    reason=f"builds wheels and a clean venv (needs network); set {_WHEEL_ENV}=1")
def test_every_readme_command_runs_from_an_installed_wheel(tmp_path: Path) -> None:
    """issue #416 D3 doc test: build pyshal + shal-arena wheels, install both
    into a clean venv, then run every README `shal-arena` command from a temp
    dir with that venv's interpreter -- the code under test is the installed
    package (package data included), not the checkout. Needs the `build`
    frontend and network (pyyaml); CI's arena job sets the env var."""
    from .test_readme_commands import _readme_bash_commands, _resolve_repo_paths

    repo = _EXAMPLES_DIR.parent.parent
    wheels = tmp_path / "wheels"
    for src in (repo, repo / "arena"):
        subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(wheels),
                        str(src)], check=True, capture_output=True, text=True, timeout=300)
    venv_dir = tmp_path / "venv"
    venv.create(venv_dir, with_pip=True)
    py = venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    subprocess.run([str(py), "-m", "pip", "install", *map(str, wheels.glob("*.whl"))],
                   check=True, capture_output=True, text=True, timeout=600)

    work = tmp_path / "work"
    work.mkdir()
    where = subprocess.run([str(py), "-c", "import shal_arena; print(shal_arena.__file__)"],
                           cwd=work, check=True, capture_output=True, text=True).stdout
    assert "site-packages" in where, where

    run_id: str | None = None
    for command in _readme_bash_commands():
        if command[0] != "shal-arena":
            continue
        tokens = [run_id if (t == "<run-id>" and run_id) else t for t in command[1:]]
        assert "<run-id>" not in tokens, command
        tokens = _resolve_repo_paths(tokens)
        proc = subprocess.run([str(py), "-m", "shal_arena.cli", *tokens], cwd=work,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=120)
        assert proc.returncode == 0, f"{command}\nstderr: {proc.stderr}"
        doc = json.loads(proc.stdout)
        assert doc.get("ok") is True, f"{command}\n{proc.stdout}"
        if tokens[0] == "run":
            run_id = doc["run_id"]


def test_examples_are_named_in_the_readme() -> None:
    readme = (_EXAMPLES_DIR.parent / "README.md").read_text(encoding="utf-8")
    assert "Write your driver" in readme
    assert "examples/minimal_psu_driver.py" in readme
    assert "examples/minimal_dmm_driver.py" in readme
