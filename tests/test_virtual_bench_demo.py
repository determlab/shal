"""tests/test_virtual_bench_demo.py — the virtual bench demo (issue #305, T8).

The committed half of the DoD: build the wheel, install it plus pytest-shal
into a clean venv, run the README's two commands for real, and check their
JSON output and exit codes. The CI job (.github/workflows/virtual-bench.yml)
runs the very same two commands daily, against the very same kind of install,
from a clean checkout.

pytest-shal isn't on PyPI (examples/demos/virtual-bench/README.md explains
why, and pins the exact commit used here); building the venv needs the
network to fetch it, so any failure doing that is reported as a skip, not a
failure — this test cannot tell "no network here" apart from "pytest-shal is
actually broken" any more precisely than that.
"""
from __future__ import annotations

import json
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "examples" / "demos" / "virtual-bench"
PYTEST_SHAL_REF = "e240b07"  # determlab/pytest-shal, merged #27/#28 — see the demo's README


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


@pytest.fixture(scope="module")
def demo_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A clean venv with this commit's wheel, pytest and pytest-shal installed."""
    tmp = tmp_path_factory.mktemp("virtual-bench")
    dist = tmp / "dist"
    build = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(dist)],
        cwd=ROOT, capture_output=True, text=True, timeout=300,
    )
    if build.returncode != 0:
        pytest.skip(f"could not build the wheel: {build.stderr[-2000:]}")
    wheels = list(dist.glob("*.whl"))
    if not wheels:
        pytest.skip("wheel build produced no .whl")

    venv_dir = tmp / "venv"
    venv.create(venv_dir, with_pip=True)
    py = _venv_python(venv_dir)

    install = subprocess.run(
        [str(py), "-m", "pip", "install", str(wheels[0]), "pytest"],
        capture_output=True, text=True, timeout=300,
    )
    if install.returncode != 0:
        pytest.skip(f"could not install the wheel into a clean venv: {install.stderr[-2000:]}")

    # --no-deps: pytest-shal's own pin (pyshal<0.4) predates pyshal#217, which
    # it actually needs and which this wheel has — see the demo's README.
    plugin = subprocess.run(
        [str(py), "-m", "pip", "install", "--no-deps",
         f"pytest-shal @ git+https://github.com/determlab/pytest-shal@{PYTEST_SHAL_REF}"],
        capture_output=True, text=True, timeout=300,
    )
    if plugin.returncode != 0:
        pytest.skip(f"could not install pytest-shal (needs network): {plugin.stderr[-2000:]}")
    return venv_dir


def _run(py: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(py), "run_bench.py", *extra], cwd=DEMO_DIR,
                          capture_output=True, text=True, timeout=120)


def test_pass_run_gives_a_pass_record_and_exit_0(demo_venv: Path) -> None:
    result = _run(_venv_python(demo_venv))
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["verdict"] == "pass"
    assert summary["cause"] is None


def test_unplug_dmm_gives_an_error_record_with_cause_transport_and_a_different_exit(
        demo_venv: Path) -> None:
    result = _run(_venv_python(demo_venv), "--unplug", "dmm")
    assert result.returncode != 0
    summary = json.loads(result.stdout)
    assert summary["verdict"] == "error"
    assert summary["cause"] == "transport"
    # "an exit code different from a failed test" (DoD): a failed check()
    # would exit 1; this is the link-never-answered code (shal#300's exit 4).
    assert result.returncode == 4
