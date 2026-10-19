"""issue #522: a cold reader gets to first success from the first screen of
README.md and AGENTS.md. One install line (the git URL pair, because
`pip install shal-arena` does not resolve yet) and one command, `shal-arena ui`.
The install string is the same in all three docs, and a clean venv resolves it."""
from __future__ import annotations

import re
import subprocess
import sys
import venv
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_README = _ROOT / "README.md"
_AGENTS = _ROOT / "AGENTS.md"
_ARENA_README = _ROOT / "arena" / "README.md"

_FENCE_RE = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
_INSTALL_PREFIX = "pip install git+https://github.com/determlab/shal "
_FIRST_SCREEN_LINES = 30


def _first_block(path: Path) -> tuple[int, str]:
    """(1-based line of the opening fence, body) of the first fenced block."""
    text = path.read_text(encoding="utf-8")
    m = _FENCE_RE.search(text)
    assert m, f"{path.name} has no fenced code block"
    return text.count("\n", 0, m.start()) + 1, m.group(2)


def _install_line(path: Path) -> str:
    _, body = _first_block(path)
    lines = [ln for ln in body.splitlines() if ln.startswith("pip install")]
    assert len(lines) == 1, f"{path.name}: first block must hold one install line"
    return lines[0]


def test_readme_first_code_block_is_the_install_line_within_30_lines() -> None:
    line, body = _first_block(_README)
    assert line <= _FIRST_SCREEN_LINES, f"README first code block opens at line {line}"
    assert body.splitlines()[0].startswith(_INSTALL_PREFIX)
    assert "#subdirectory=arena" in body.splitlines()[0]


@pytest.mark.parametrize("path", [_README, _AGENTS], ids=lambda p: p.name)
def test_first_block_names_shal_arena_ui_as_the_command_to_first_success(path: Path) -> None:
    _, body = _first_block(path)
    assert body.splitlines()[-1].strip() == "shal-arena ui"


def test_agents_first_block_is_within_the_first_30_lines() -> None:
    line, _ = _first_block(_AGENTS)
    assert line <= _FIRST_SCREEN_LINES


def test_install_line_is_the_same_string_in_readme_agents_and_arena_readme() -> None:
    readme = _install_line(_README)
    assert _install_line(_AGENTS) == readme
    arena = _ARENA_README.read_text(encoding="utf-8")
    for spec in re.findall(r'"?(git\+https://\S+?)"?(?:\s|$)', readme):
        assert spec in arena, f"arena/README.md does not show {spec}"


def test_install_line_resolves_in_a_clean_venv(tmp_path: Path) -> None:
    """The README string, as printed, run from a fresh venv. `--dry-run`: it
    resolves and fetches metadata, so a dependency that cannot be met (e.g.
    pyshal pinned past what main builds) fails here, without installing."""
    venv.create(tmp_path / "v", with_pip=True)
    py = tmp_path / "v" / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    import shlex
    argv = shlex.split(_install_line(_README))
    proc = subprocess.run([str(py), "-m", *argv[:2], "--dry-run", *argv[2:]],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
