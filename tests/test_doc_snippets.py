"""The `shal.load(dict)` and `config:` examples in the docs run as written (#44).

Each test takes the first ```python block after a marker line in README.md or
src/shal/SDK.md (the page `shal docs --sdk` prints) and runs it. Change the
snippet and this test runs the new one; break it and this test fails.
"""
from __future__ import annotations

import os
import re
import sys
import venv as venv_module
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
README = _ROOT / "README.md"
SDK = _ROOT / "src" / "shal" / "SDK.md"
AGENTS = _ROOT / "AGENTS.md"

sys.path.insert(0, str(_ROOT / "dev" / "quickstart"))
import run_readme  # noqa: E402
from run_readme import (  # noqa: E402
    MAIN_ONLY_MARK,
    StepFailed,
    doc_blocks,
    doc_test_plan,
    first_screen_end,
    run_doc_steps,
)


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


# --- #342: every ```bash/```sh block on README's first screen (top of file through
# the heading right after Quick Start) and in AGENTS.md runs as printed, in a temp
# venv — the same guarantee #354 above already gives the inline `shal ...` mentions,
# extended to the fenced commands a reader actually copy-pastes. A block that cannot
# run here (needs hardware/secrets, or — like the "git clone" dev-install step —
# shell syntax this runner does not interpret) carries `<!-- doc-test: skip REASON
# -->`; MAX_DOC_TEST_SKIPS caps how many of those a doc may carry before this test
# itself fails, so a skip can't quietly cover for a command that should run. With
# `RC_WHEELS=<dir>` set, the `pip install` lines resolve from that directory
# (`--no-index --find-links`) instead of PyPI, and a skip whose reason names
# `MAIN_ONLY_MARK` runs too (shal#361) — the release-candidate wheel has what PyPI
# doesn't. Without `RC_WHEELS`, nothing here changes: those blocks stay skipped.
#
# shal#362/#368: without `RC_WHEELS`, the default mode carries exactly 2 skips —
# README's "git clone ... && cd shal" dev-install step and its `docs
# --samples`/`--sample` block (both `MAIN_ONLY_MARK`-tagged, not yet on PyPI).
# shal#384: a third, README's `docs --sample virtual-bench ... && python ...` block
# (a `&&` chain, and it needs pytest-shal, which this venv lacks); AGENTS.md carries
# the same block as its one skip. The cap is set to that count on purpose, not padded,
# so a fourth skip anywhere fails this test immediately instead of waiting for a later
# audit to notice.

MAX_DOC_TEST_SKIPS = 3


def _rc_wheels() -> Path | None:
    v = os.environ.get("RC_WHEELS")
    return Path(v) if v else None


@pytest.fixture(scope="module")
def doc_test_venv(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One clean venv shared by every doc-snippet test in this module — `pip install
    pyshal` only needs to happen once."""
    venv_dir = tmp_path_factory.mktemp("doc-test-venv")
    venv_module.create(venv_dir, with_pip=True)
    return venv_dir


def _run_doc_test(doc: Path, blocks: list[run_readme.Block], venv: Path, workdir: Path) -> None:
    find_links = _rc_wheels()
    steps, skips = doc_test_plan(blocks, rc_wheels=find_links is not None)
    for line, reason in skips:
        print(f"SKIP {doc.name}:{line}: {reason}")
    assert len(skips) <= MAX_DOC_TEST_SKIPS, (
        f"{doc.name} carries {len(skips)} doc-test skips (cap is {MAX_DOC_TEST_SKIPS}); "
        f"either run the block or lower the cap deliberately:\n{skips}")
    run_doc_steps(steps, venv, workdir, find_links=find_links)


def test_readme_first_screen_commands_run_in_a_temp_venv(
        doc_test_venv: Path, tmp_path: Path) -> None:
    text = README.read_text(encoding="utf-8")
    blocks = doc_blocks(text, 0, first_screen_end(text))
    _run_doc_test(README, blocks, doc_test_venv, tmp_path)


def test_agents_md_commands_run_in_a_temp_venv(doc_test_venv: Path, tmp_path: Path) -> None:
    blocks = doc_blocks(AGENTS.read_text(encoding="utf-8"))
    _run_doc_test(AGENTS, blocks, doc_test_venv, tmp_path)


def test_a_third_doc_test_skip_fails_naming_the_cap(tmp_path: Path) -> None:
    """Agent path (shal#362/#368): a doc carrying one more `doc-test: skip` than
    `MAX_DOC_TEST_SKIPS` allows fails this test before any command runs, naming the
    actual count and the cap — the skip-count check happens before the venv is
    touched, so this stays fast and offline."""
    fake_doc = tmp_path / "fake.md"
    skip_count = MAX_DOC_TEST_SKIPS + 1
    fake_doc.write_text(
        "\n\n".join(
            f"<!-- doc-test: skip block {i} -->\n\n```bash\ntrue\n```"
            for i in range(skip_count)
        ),
        encoding="utf-8",
    )
    blocks = doc_blocks(fake_doc.read_text(encoding="utf-8"))

    with pytest.raises(
        AssertionError,
        match=rf"carries {skip_count} doc-test skips \(cap is {MAX_DOC_TEST_SKIPS}\)",
    ):
        _run_doc_test(fake_doc, blocks, tmp_path / "unused-venv", tmp_path / "unused-workdir")


def test_a_bad_install_line_fails_naming_the_block_and_its_line_number(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The deliberate-failure case (#342): a bad `pip install` line must fail the run
    with the offending command and its source line in the message, not just a bare
    pip traceback. `pip`/the venv are faked (`_fake_run`'s pattern in
    test_quickstart_readme.py) so this stays fast and offline — the real network
    install is exercised by the two tests above."""
    fake_readme = tmp_path / "README.md"
    fake_readme.write_text(
        "## Quick Start\n\n```bash\npip install nonexistent-pkg-xyz\n```\n", encoding="utf-8")

    def fake_run(argv: list[str], **_kw: object) -> run_readme.subprocess.CompletedProcess:
        assert argv[:2] == ["pip", "install"]
        return run_readme.subprocess.CompletedProcess(
            argv, 1, b"ERROR: No matching distribution found for nonexistent-pkg-xyz\n")

    monkeypatch.setattr(run_readme.subprocess, "run", fake_run)
    monkeypatch.setattr(run_readme.shutil, "which", lambda name, path=None: name)

    text = fake_readme.read_text(encoding="utf-8")
    blocks = doc_blocks(text, 0, first_screen_end(text))
    steps, skips = doc_test_plan(blocks)
    assert skips == []
    with pytest.raises(StepFailed, match=r"line 3: `pip install nonexistent-pkg-xyz` "
                                         r"exited 1, expected 0"):
        run_doc_steps(steps, tmp_path / "venv", tmp_path)
