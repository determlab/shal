"""shal-arena is "packaged as shal-arena (own pyproject, version follows
shal)" (issue #310 Scope). Pins the two versions together so a release that
bumps one without the other fails loudly here instead of drifting silently."""
from __future__ import annotations

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
