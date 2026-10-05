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
import re
import shlex
import subprocess
import sys
import venv
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "examples" / "demos" / "virtual-bench"
PYTEST_SHAL_REF = "9eb77552aa1618783a96201140765b1492525aaf"  # pytest-shal main; same as README
README = DEMO_DIR / "README.md"
MIN_PYSHAL = (0, 4)


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

    plugin = subprocess.run(
        [str(py), "-m", "pip", "install",
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


def _readme_install_sources() -> list[str]:
    """Every requirement named by a `pip install` line in the README's Install block."""
    text = README.read_text(encoding="utf-8")
    section = text.split("## Install", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```bash\n(.*?)```", section, re.S)
    assert block, "README '## Install' has no ```bash block"
    sources: list[str] = []
    for line in block.group(1).splitlines():
        parts = shlex.split(line)
        if parts[:2] != ["pip", "install"]:
            continue
        sources += [p for p in parts[2:] if not p.startswith("-")]
    assert sources, "README install block has no pip install lines"
    return sources


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("+")[0])[:3])


def test_readme_install_sources_resolve(tmp_path: Path) -> None:
    for src in _readme_install_sources():
        git = re.fullmatch(r"[\w.-]+ @ git\+(https://\S+?)@(\S+)", src)
        if git:
            url, ref = git.groups()
            assert re.fullmatch(r"[0-9a-f]{40}", ref), f"{src}: pin a full 40-char commit SHA"
            repo = tmp_path / re.sub(r"\W", "_", url)
            subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True)
            fetch = subprocess.run(
                ["git", "-C", str(repo), "fetch", "-q", "--depth", "1", url, ref],
                capture_output=True, text=True, timeout=120)
            assert fetch.returncode == 0, f"{src}: commit not fetchable: {fetch.stderr[-500:]}"
            continue
        plain = re.fullmatch(r"([A-Za-z0-9_.-]+)(?:==([\w.]+))?", src)
        assert plain, f"{src}: not a git+https pin, a bare name or name==version"
        name, version = plain.groups()
        out = subprocess.run([sys.executable, "-m", "pip", "index", "versions", name],
                             capture_output=True, text=True, timeout=120)
        assert out.returncode == 0, f"{src}: not installable from the index: {out.stderr[-500:]}"
        found = re.search(r"Available versions: (.*)", out.stdout)
        assert found, out.stdout
        versions = [v.strip() for v in found.group(1).split(",")]
        if version:
            assert version in versions, f"{src}: no such version; index has {versions}"
        else:
            version = versions[0]  # newest first: what a bare install gets
        if name == "pyshal":
            assert _version_tuple(version) >= MIN_PYSHAL, (
                f"{src}: resolves to pyshal {version}, the demo needs >= 0.4.0")
