"""Every block in docs/GUIDE.md marked `# runs-on-sim` really runs (issue #9).

A fenced block whose first line is the comment `# runs-on-sim` needs no hardware and
no network. YAML blocks must load with `shal.load()`, Python blocks must execute, bash
blocks must exit 0. Each runs in a fresh temp folder, so a block writes its own files.
Hardware and SSH steps carry no marker and are not run.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GUIDE = Path(__file__).parent.parent / "docs" / "GUIDE.md"
_BLOCK = re.compile(r"^```(\w+)\n# runs-on-sim\n(.*?)^```", re.DOTALL | re.MULTILINE)


def _blocks() -> list[tuple[str, str]]:
    text = _GUIDE.read_text(encoding="utf-8").replace("\r\n", "\n")
    return [(m.group(1), "# runs-on-sim\n" + m.group(2)) for m in _BLOCK.finditer(text)]


def _env() -> dict:
    bindir = str(Path(sys.executable).parent)
    return {**os.environ, "PATH": bindir + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1"}


def _run(argv, cwd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=_env(), capture_output=True, text=True,
                          encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL, **kw)


def test_the_guide_has_sim_blocks():
    assert len(_blocks()) >= 2


@pytest.mark.parametrize("lang,code", _blocks() or [("none", "")])
def test_sim_block_runs(lang, code, tmp_path):
    if lang == "yaml":
        import shal
        path = tmp_path / "topology.yaml"
        path.write_text(code, encoding="utf-8")
        shal.load(str(path))
    elif lang == "python":
        r = _run([sys.executable, "-c", code], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
    elif lang == "bash":
        bash = shutil.which("bash")
        if bash is None:
            pytest.skip("no bash on PATH")
        if sys.platform == "win32" and "system32" in bash.replace("/", "\\").lower():
            pytest.skip("bash is the System32 WSL launcher, not a POSIX shell for this test")
        # a `shal` on PATH is the venv's console script; fall back to the module
        shim = tmp_path / "shal"
        shim.write_text(
            f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -m shal.cli "$@"\n',
            encoding="utf-8")
        os.chmod(shim, 0o755)
        r = _run([bash, "-c", 'export PATH="$PWD:$PATH"\n' + code], tmp_path)
        assert r.returncode == 0, r.stdout + r.stderr
    else:
        pytest.fail(f"a runs-on-sim block must be yaml, python or bash, not {lang!r}")
