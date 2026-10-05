"""The `shal.load(dict)` and `config:` examples in the docs run as written (#44).

Each test takes the first ```python block after a marker line in README.md or
src/shal/SDK.md (the page `shal docs --sdk` prints) and runs it. Change the
snippet and this test runs the new one; break it and this test fails.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
README = _ROOT / "README.md"
SDK = _ROOT / "src" / "shal" / "SDK.md"
AGENTS = _ROOT / "AGENTS.md"


def _snippet(doc: Path, marker: str) -> str:
    text = doc.read_text(encoding="utf-8")
    at = text.index(marker)  # ValueError here: the marker moved; update this test
    m = re.compile(r"^```python\n(.*?)^```$", re.M | re.S).search(text, at)
    assert m, f"{doc.name}: no ```python block after {marker!r}"
    return m.group(1)


def _run(code: str, capsys: pytest.CaptureFixture[str]) -> str:
    exec(compile(code, "<doc snippet>", "exec"), {"__name__": "__doc_snippet__"})
    return capsys.readouterr().out


@pytest.mark.parametrize("doc, marker", [
    (README, "## Load a topology from a dict"),
    (SDK, "**A topology can be a dict.**"),
])
def test_load_dict_snippet_reads_the_sim_sensor(
        doc: Path, marker: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = _snippet(doc, marker)
    assert "shal.load(topology)" in code
    out = _run(code, capsys).strip()
    float(out)  # one live reading from the sim sensor


def test_config_snippet_reads_node_spec_config_with_env_resolved(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SITE", "unset")  # the snippet sets it; teardown removes it
    code = _snippet(SDK, "**A node's `config:` lives in `self.node.spec[\"config\"]`.**")
    assert 'self.node.spec.get("config", {})' in code
    assert _run(code, capsys).strip() == "hello from lab-3"


# --- #354: every `shal` command/flag README.md and AGENTS.md show that pyshal
# 0.3.0 (the latest PyPI release) does not have must carry this mark, so a cold
# agent that just ran `pip install pyshal` is never told to run a command that
# fails. ---

MAIN_ONLY_MARK = "main only, not in the PyPI release yet"

# The `shal` subcommands and flags pyshal 0.3.0's CLI actually has (CHANGELOG.md
# "## [0.3.0]" and earlier). `routes` and `records` are 0.4.0; so are the
# `docs --samples`/`--sample`/`--to` flags, `--via` and `--skip-newer`.
RELEASED_SUBCOMMANDS = {"probe", "tools", "call", "check", "docs", "mcp"}
RELEASED_FLAGS = {"--json", "--drivers", "--list", "--example", "--sdk", "--topology", "--help"}

_BACKTICK_INVOCATION = re.compile(r"`shal ([^`\n]+)`")


def _invocations(line: str) -> list[str]:
    """Candidate `shal ...` invocation strings found on one line."""
    found = list(_BACKTICK_INVOCATION.findall(line))
    stripped = line.strip()
    if stripped.startswith("shal "):
        found.append(stripped[len("shal "):].split("#", 1)[0].strip())
    return found


def _unreleased_tokens(invocation: str) -> list[str]:
    """Subcommand/flag words in `invocation` that pyshal 0.3.0's CLI lacks."""
    tokens = invocation.split()
    if not tokens:
        return []
    bad = []
    first = tokens[0].rstrip(":.,")
    if first.startswith("--"):
        if first not in RELEASED_FLAGS:
            bad.append(first)
    elif first not in RELEASED_SUBCOMMANDS:
        bad.append(first)
    for tok in tokens[1:]:
        tok = tok.rstrip(":.,")
        if tok.startswith("--") and tok not in RELEASED_FLAGS:
            bad.append(tok)
    return bad


def _paragraphs(text: str) -> list[tuple[int, int, str]]:
    """[(first_line, last_line, text), ...], 1-indexed; blank lines and a new
    `- ` bullet both start a new paragraph, so one bullet point is one unit."""
    lines = text.splitlines()
    paras: list[tuple[int, int, str]] = []
    start: int | None = None
    buf: list[str] = []

    def flush(end_line: int) -> None:
        nonlocal start, buf
        if buf:
            paras.append((start, end_line, "\n".join(buf)))
        start, buf = None, []

    for i, line in enumerate(lines, start=1):
        if line.strip() == "":
            flush(i - 1)
            continue
        if line.lstrip().startswith("- ") and buf:
            flush(i - 1)
        if start is None:
            start = i
        buf.append(line)
    flush(len(lines))
    return paras


def _unmarked_unreleased_commands(doc: Path) -> list[str]:
    text = doc.read_text(encoding="utf-8")
    paras = _paragraphs(text)
    violations = []
    for i, (start, _end, ptext) in enumerate(paras):
        offenders = [
            tok
            for line in ptext.splitlines()
            for inv in _invocations(line)
            for tok in _unreleased_tokens(inv)
        ]
        if not offenders:
            continue
        prev_text = paras[i - 1][2] if i > 0 else ""
        if MAIN_ONLY_MARK not in ptext and MAIN_ONLY_MARK not in prev_text:
            violations.append(f"{doc.name}:{start}: {sorted(set(offenders))} not marked"
                               f" ({MAIN_ONLY_MARK!r} missing on this line or the line above)")
    return violations


def test_released_commands_other_than_whitelisted_are_marked_in_docs() -> None:
    violations = _unmarked_unreleased_commands(AGENTS) + _unmarked_unreleased_commands(README)
    assert not violations, "\n".join(violations)
