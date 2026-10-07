"""CTO review on #407 round 2, must-fix 1: `registry.register(cls,
override=True)` silently drops every other claimant for the same
`compatible`, giving no warning -- whichever file was imported last wins.
These are the files a real player copies (`arena/examples/*`) or Play
itself loads on every call (`shal_arena.reference_drivers`); neither needs
it, since the real fix for a repeated import of the SAME file is caching
the import itself (`loader.once_per_path`, used by `ui/server.py` and
`bench.py`), not evicting whatever else claims the compatible.
"""
from __future__ import annotations

from pathlib import Path

_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
_REFERENCE_DRIVERS_DIR = (Path(__file__).resolve().parent.parent
                         / "src" / "shal_arena" / "reference_drivers")


def _driver_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def test_examples_never_use_override() -> None:
    files = _driver_files(_EXAMPLES_DIR)
    assert files  # the glob itself found real files, not an empty/renamed dir
    offenders = [p for p in files if "override" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_reference_drivers_never_use_override() -> None:
    files = _driver_files(_REFERENCE_DRIVERS_DIR)
    assert files
    offenders = [p for p in files if "override" in p.read_text(encoding="utf-8")]
    assert offenders == []
