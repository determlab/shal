"""shal-arena is "packaged as shal-arena (own pyproject, version follows
shal)" (issue #310 Scope). Pins the two versions together so a release that
bumps one without the other fails loudly here instead of drifting silently."""
from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 has no stdlib tomllib
    tomllib = None

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(tomllib is None, reason="needs Python 3.11+ (stdlib tomllib)")


def _version(pyproject: Path) -> str:
    with pyproject.open("rb") as f:
        return tomllib.load(f)["project"]["version"]


def test_arena_version_follows_shal() -> None:
    shal_version = _version(_REPO_ROOT / "pyproject.toml")
    arena_version = _version(_REPO_ROOT / "arena" / "pyproject.toml")
    assert arena_version == shal_version, (
        f"arena/pyproject.toml version ({arena_version!r}) must follow "
        f"pyshal's ({shal_version!r}) — issue #310 Scope")


def test_installed_wheel_has_card_sim_catalogue(tmp_path: Path) -> None:
    """CTO review, issue #313 (PR #323): `[tool.setuptools.package-data]`
    missed `card_sim/catalogue/*.yaml`, so a REAL install had none of it —
    every test up to now ran from an editable install (this repo's own
    `arena` CI job runs `pip install -e "./arena[dev]"`), which reads
    straight off the repo tree and never exercises package-data at all.
    Build the actual wheel, the same way CI's `packaging`/`build` jobs build
    pyshal's, and look inside the archive rather than trusting
    pyproject.toml's text."""
    pytest.importorskip("build")
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path),
         str(_REPO_ROOT / "arena")],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    wheels = list(tmp_path.glob("*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert any(n.endswith("shal_arena/card_sim/catalogue/instruments.yaml") for n in names), (
        f"card_sim/catalogue/*.yaml did not make it into the wheel: {names}")
