"""#282: a load error from text-mode `shal probe` / `shal tools` is one line that
names a real fix (`shal docs`), never a traceback or an examples/ path."""
from __future__ import annotations

import subprocess
import sys

import pytest


def _shal(*argv: str, cwd) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def _check(r: subprocess.CompletedProcess) -> None:
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "Traceback" not in r.stderr
    assert "shal docs" in r.stderr
    assert "examples/" not in r.stderr
    assert len(r.stderr.strip().splitlines()) == 1, r.stderr


@pytest.mark.parametrize("cmd", ["probe", "tools"])
def test_bad_schema_is_one_line_with_a_fix(tmp_path, cmd):
    bad = tmp_path / "bad.yaml"
    bad.write_text("shal_version: 1\nroot:\n  bus:\n    driver: 42\n    address: [\n",
                   encoding="utf-8")
    _check(_shal(cmd, "bad.yaml", cwd=tmp_path))


def test_bad_schema_valid_yaml(tmp_path):
    (tmp_path / "bad.yaml").write_text("shal_version: 1\nroot: nope\n", encoding="utf-8")
    _check(_shal("probe", "bad.yaml", cwd=tmp_path))


@pytest.mark.parametrize("cmd", ["probe", "tools"])
def test_missing_file_is_one_line_with_a_fix(tmp_path, cmd):
    _check(_shal(cmd, "missing.yaml", cwd=tmp_path))
