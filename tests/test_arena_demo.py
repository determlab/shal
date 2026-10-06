"""issue #410: `shal-arena demo` runs the whole story from the installed
package, no clone, no config -- the Done-when runs this exact CLI form
non-interactively, in a temp dir, and checks `record_path`/`card_path` are
real, absolute files."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("shal_arena")

_TIMEOUT = 180


def test_demo_json_names_real_absolute_record_and_card_paths(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "demo", "--pause", "0", "--json"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=_TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True

    record_path, card_path = Path(doc["record_path"]), Path(doc["card_path"])
    assert record_path.is_absolute() and record_path.is_file()
    assert card_path.is_absolute() and card_path.is_file()


def test_demo_default_pause_is_zero_under_json(tmp_path: Path) -> None:
    # issue #410: "--pause S ... (default 0 under --json)" -- with no
    # --pause, 8 steps at the plain-mode default (2.0 s each) would take at
    # least 16 s; this must finish in well under that.
    import time

    start = time.monotonic()
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "demo", "--json"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=_TIMEOUT)
    elapsed = time.monotonic() - start
    assert proc.returncode == 0, proc.stderr
    assert elapsed < 10, f"took {elapsed:.1f}s -- --pause did not default to 0 under --json"
