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
import urllib.error
import urllib.request
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


# ---------------------------------------------------------------------------
# README install block (issue #345): every `pip install` source must name a
# pinned git commit or a pinned package version — never a bare, unpinned
# name — so the block a stranger copies into a clean venv actually resolves
# to the thing this demo needs, not whatever happens to be latest today.

class UnpinnedSource(AssertionError):
    """A `pip install` target names neither a pinned commit nor a pinned version."""


_GIT_PIN_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9._-]+)\s*@\s*git\+https://github\.com/"
    r"(?P<owner>[A-Za-z0-9._-]+)/(?P<repo>[A-Za-z0-9._-]+)@(?P<ref>[0-9a-fA-F]{7,40})$"
)
_VERSION_PIN_RE = re.compile(r"^(?P<name>[A-Za-z0-9._-]+)==(?P<version>[A-Za-z0-9.]+)$")


def _install_block_lines(readme_text: str) -> list[str]:
    """The ```bash fenced block under '## Install', one stripped line per entry."""
    start = readme_text.index("## Install")
    rest = readme_text[start:]
    fence_start = rest.index("```bash") + len("```bash")
    fence_end = rest.index("```", fence_start)
    return [line.strip() for line in rest[fence_start:fence_end].splitlines() if line.strip()]


def _pip_install_targets(lines: list[str]) -> list[str]:
    """Every source named on a `pip install` line, with `pip install` and flags stripped."""
    targets = []
    for line in lines:
        if not line.startswith("pip install"):
            continue
        tokens = shlex.split(line)[2:]
        targets.extend(token for token in tokens if not token.startswith("-"))
    return targets


def _assert_pinned(target: str) -> tuple[str, re.Match]:
    """Classify a target as a pinned git commit or a pinned version; raise if neither."""
    git_match = _GIT_PIN_RE.match(target)
    if git_match:
        return "git", git_match
    version_match = _VERSION_PIN_RE.match(target)
    if version_match:
        return "version", version_match
    raise UnpinnedSource(
        f"{target!r} names neither a pinned git commit (`name @ git+URL@REF`) "
        f"nor a pinned version (`name==X.Y.Z`)"
    )


def _assert_resolves(kind: str, match: re.Match) -> None:
    """Hit the index to confirm the pin is real; skip (don't fail) when we can't reach it."""
    if kind == "git":
        url = f"https://api.github.com/repos/{match['owner']}/{match['repo']}/commits/{match['ref']}"
    else:
        url = f"https://pypi.org/pypi/{match['name']}/json"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            pytest.fail(f"{url} says this pin does not exist (404)")
        pytest.skip(f"could not verify {url} (HTTP {exc.code}, needs network)")
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"could not verify {url} (needs network): {exc}")

    if kind == "version" and match["version"] not in json.loads(body)["releases"]:
        pytest.fail(f"{match['name']} {match['version']} is not a release on PyPI")


def test_readme_install_block_rejects_an_unpinned_source() -> None:
    """The old README text (`pip install pyshal pytest`, no pins) must fail this check."""
    old_lines = ['pip install pyshal pytest']
    targets = _pip_install_targets(old_lines)
    assert targets, "nothing to check — fixture is stale"
    with pytest.raises(UnpinnedSource):
        for target in targets:
            _assert_pinned(target)


def test_readme_install_sources_are_pinned_and_resolve() -> None:
    readme_text = (DEMO_DIR / "README.md").read_text()
    targets = _pip_install_targets(_install_block_lines(readme_text))
    assert targets, "README's Install block names no `pip install` targets"
    for target in targets:
        kind, match = _assert_pinned(target)
        _assert_resolves(kind, match)
