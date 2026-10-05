"""The README Quick Start runner (#159): extraction and output matching.

The runner lives under dev/quickstart/ (never shipped in the wheel); add it to
sys.path so this test can import it. The real run — a clean venv, the built
wheel, the real README — is the `quickstart` CI job, not this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "quickstart"))

import run_readme  # noqa: E402
from run_readme import (  # noqa: E402
    ReadmeError,
    Run,
    Save,
    doc_blocks,
    doc_test_plan,
    first_screen_end,
    output_matches,
    plan,
    quickstart_blocks,
    rc_wheels_argv,
    substitute_wheel,
)

FENCE = "```"

FAKE = f"""# Project

## Install

{FENCE}bash
pip install something-else
{FENCE}

## Quick Start

{FENCE}bash
pip install pyshal   # the package
{FENCE}

Save this as `sim.yaml`.

{FENCE}yaml
root: {{}}
{FENCE}

Now read it:

{FENCE}bash
shal probe sim.yaml
{FENCE}

{FENCE}
# 1 read(s) on this topology
ambient_temp__read_celsius: 25.59
{FENCE}

### From your own code

Save this as `quickstart.py`:

{FENCE}python
print(1.5)
{FENCE}

{FENCE}bash
shal call sim.yaml ambient_temp set_target 30
{FENCE}

{FENCE}
refused
{FENCE}

It exits 2 and nothing was sent.

## Next section

{FENCE}bash
not part of the quick start
{FENCE}
"""


def test_blocks_extracted_in_order_and_only_from_the_section() -> None:
    blocks = quickstart_blocks(FAKE)
    assert [b.lang for b in blocks] == ["bash", "yaml", "bash", "", "python", "bash", ""]
    steps = plan(blocks)
    kinds = [(type(s).__name__, s.name if isinstance(s, Save) else s.raw) for s in steps]
    assert kinds == [
        ("Run", "pip install pyshal   # the package"),
        ("Save", "sim.yaml"),
        ("Run", "shal probe sim.yaml"),
        ("Save", "quickstart.py"),
        ("Run", "shal call sim.yaml ambient_temp set_target 30"),
    ]
    pip, _, probe, _, call = steps
    assert isinstance(pip, Run) and pip.argv == ["pip", "install", "pyshal"] and pip.installs
    assert isinstance(probe, Run) and probe.reads and probe.exit_code == 0
    assert probe.expected == ["# 1 read(s) on this topology",
                              "ambient_temp__read_celsius: 25.59"]
    assert isinstance(call, Run) and call.exit_code == 2 and not call.reads


def test_prompted_block_splits_commands_from_output() -> None:
    md = (f"## Quick Start\n\n{FENCE}console\n"
          f"$ shal probe sim.yaml\nok\n$ shal tools sim.yaml\n{FENCE}\n")
    a, b = plan(quickstart_blocks(md))
    assert isinstance(a, Run) and a.argv == ["shal", "probe", "sim.yaml"] and a.expected == ["ok"]
    assert isinstance(b, Run) and b.expected == []


SHOWN = ["# 1 read(s) on this topology", "ambient_temp__read_celsius: 25.59",
         "# writes — not run by `shal probe`; use `shal call` "
         "(gated ops are refused until approved): ambient_temp__set_target"]


def test_exact_output_matches() -> None:
    assert output_matches(SHOWN, "\r\n".join(SHOWN) + "\r\n")


def test_a_changed_word_fails() -> None:
    printed = list(SHOWN)
    printed[2] = printed[2].replace("writes", "write")
    assert not output_matches(SHOWN, "\n".join(printed))
    assert not output_matches(SHOWN, "\n".join(SHOWN[:2]))  # a missing line fails too


def test_the_drifting_reading_matches_by_pattern_but_not_its_label() -> None:
    for value in ("24.6", "25.84", "-3.0", "1e-05"):
        printed = [SHOWN[0], f"ambient_temp__read_celsius: {value}", SHOWN[2]]
        assert output_matches(SHOWN, "\n".join(printed)), value
    relabelled = [SHOWN[0], "ambient_temp__read_fahrenheit: 25.59", SHOWN[2]]
    assert not output_matches(SHOWN, "\n".join(relabelled))
    not_a_float = [SHOWN[0], "ambient_temp__read_celsius: warm", SHOWN[2]]
    assert not output_matches(SHOWN, "\n".join(not_a_float))
    # an integer line is not a reading: `1` must stay `1`
    assert not output_matches(SHOWN, "\n".join(["# 2 read(s) on this topology", *SHOWN[1:]]))


def test_a_bare_float_is_a_reading() -> None:
    assert output_matches(["25.59"], "24.1\n")
    assert not output_matches(["25.59"], "reading 24.1\n")


def test_a_file_block_without_a_save_name_fails_clearly() -> None:
    md = f"## Quick Start\n\nThe same, from Python:\n\n{FENCE}python\nprint(1)\n{FENCE}\n"
    never_named = r"line 5: the ```python block is never given a file name"
    with pytest.raises(ReadmeError, match=never_named):
        plan(quickstart_blocks(md))


def test_output_block_without_a_command_fails() -> None:
    md = f"## Quick Start\n\n{FENCE}\nhello\n{FENCE}\n"
    with pytest.raises(ReadmeError, match="does not follow a command"):
        plan(quickstart_blocks(md))


def test_shell_syntax_the_runner_cannot_run_fails() -> None:
    md = f"## Quick Start\n\n{FENCE}bash\nshal docs | head -1\n{FENCE}\n"
    with pytest.raises(ReadmeError, match="shell syntax"):
        plan(quickstart_blocks(md))


def test_pip_line_installs_the_wheel_in_place_of_the_package() -> None:
    wheel = Path("dist") / "pyshal-0.2.2-py3-none-any.whl"
    assert substitute_wheel(["pip", "install", "pyshal"], wheel) == ["pip", "install", str(wheel)]
    assert substitute_wheel(["pip", "install", "pyshal[mcp]"], wheel)[-1] == f"{wheel}[mcp]"
    with pytest.raises(ReadmeError, match="does not install pyshal"):
        substitute_wheel(["pip", "install", "shal"], wheel)


# --- published mode (#188): the pip line verbatim, against PyPI -----------------
# No network: subprocess.run is a fake that plays pip, the venv's python and shal.

PUBLISHED = f"""## Quick Start

{FENCE}bash
pip install pyshal
{FENCE}

Save this as `sim.yaml`.

{FENCE}yaml
root: {{}}
{FENCE}

{FENCE}bash
shal probe sim.yaml
{FENCE}

{FENCE}
ambient_temp__read_celsius: 25.59
{FENCE}
"""

NO_DRIVER = "shal probe: no driver installed for compatible 'shal,sim-sensor'"


def _fake_run(monkeypatch: pytest.MonkeyPatch, *, pip_rc: int = 0,
              probe: tuple[int, str] = (0, "ambient_temp__read_celsius: 24.9\n"),
              ) -> list[list[str]]:
    calls: list[list[str]] = []

    def run(argv, **_kw):  # type: ignore[no-untyped-def]
        calls.append(list(argv))
        if argv[0] == "pip":
            rc, out = pip_rc, "Successfully installed pyshal-0.2.2\n"
        elif argv[0] == "python" and "importlib.metadata" in argv[-1]:
            rc, out = 0, "0.2.2\n"
        elif argv[:2] == ["shal", "probe"]:
            rc, out = probe
        else:
            raise AssertionError(f"unexpected command {argv}")
        return run_readme.subprocess.CompletedProcess(argv, rc, out.encode())

    monkeypatch.setattr(run_readme.subprocess, "run", run)
    monkeypatch.setattr(run_readme.shutil, "which", lambda name, path=None: name)
    return calls


def _published(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], Path]:
    readme = tmp_path / "README.md"
    readme.write_text(PUBLISHED, encoding="utf-8")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return ["--venv", str(tmp_path / "venv"), "--published", "--readme", str(readme)], summary


def test_published_runs_the_pip_line_verbatim_and_reports_the_version(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    calls = _fake_run(monkeypatch)
    args, summary = _published(tmp_path, monkeypatch)
    assert run_readme.main(args) == 0
    assert calls[0] == ["pip", "install", "pyshal"]  # not rewritten to a wheel
    out = capsys.readouterr().out
    assert "$ pip install pyshal\n" in out and "[run as:" not in out
    assert "--- installed from PyPI: pyshal 0.2.2" in out
    assert ("README Quick Start passed against published pyshal 0.2.2: `pip install` to first "
            "successful read in ") in out
    written = summary.read_text(encoding="utf-8")
    assert written.startswith("README Quick Start passed against published pyshal 0.2.2: ")
    assert " s (" in written


def test_published_failure_names_the_installed_version(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    _fake_run(monkeypatch, probe=(1, NO_DRIVER + "\n"))
    args, summary = _published(tmp_path, monkeypatch)
    assert run_readme.main(args) == 1
    out = capsys.readouterr().out
    assert NO_DRIVER in out
    assert ("README Quick Start FAILED against published pyshal 0.2.2: README line 13: "
            "`shal probe sim.yaml` exited 1, the README expects 0") in out
    assert summary.read_text(encoding="utf-8").startswith(
        "README Quick Start FAILED against published pyshal 0.2.2: README line 13")


def test_published_failed_pip_says_no_version_was_installed(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]) -> None:
    calls = _fake_run(monkeypatch, pip_rc=1)
    args, _ = _published(tmp_path, monkeypatch)
    assert run_readme.main(args) == 1
    assert calls == [["pip", "install", "pyshal"]]
    assert ("FAILED against published pyshal (version unknown: not installed): "
            "README line 3: `pip install pyshal` exited 1") in capsys.readouterr().out


def test_dist_and_published_are_one_or_the_other(tmp_path: Path) -> None:
    for args in (["--venv", "v"], ["--venv", "v", "--published", "--dist", str(tmp_path)]):
        with pytest.raises(SystemExit):
            run_readme.main(args)


# --- doc_blocks / doc_test_plan / rc_wheels_argv (#342): README's first screen and
# AGENTS.md are bigger than the Quick Start section alone, and hold illustrative
# yaml/python blocks with no "Save this as" prose — quickstart_blocks()/plan() would
# treat those as a README defect. The lenient counterpart below must not. ----------

MULTI_SECTION = f"""## Install

{FENCE}bash
pip install something
{FENCE}

## Quick Start

{FENCE}bash
shal probe sim.yaml
{FENCE}

### A subsection

{FENCE}bash
shal tools sim.yaml
{FENCE}

## Next section

{FENCE}bash
not part of the first screen
{FENCE}
"""


def test_doc_blocks_does_not_stop_at_a_heading_unlike_quickstart_blocks() -> None:
    blocks = doc_blocks(MULTI_SECTION)
    assert [b.text.strip() for b in blocks] == [
        "pip install something", "shal probe sim.yaml", "shal tools sim.yaml",
        "not part of the first screen",
    ]


def test_first_screen_end_is_the_heading_right_after_quick_start() -> None:
    lines = MULTI_SECTION.splitlines()
    end = first_screen_end(MULTI_SECTION)
    assert lines[end] == "## Next section"
    blocks = doc_blocks(MULTI_SECTION, 0, end)
    assert [b.text.strip() for b in blocks] == [
        "pip install something", "shal probe sim.yaml", "shal tools sim.yaml",
    ]


def test_first_screen_end_requires_exactly_one_quick_start_heading() -> None:
    with pytest.raises(ReadmeError, match="found 0"):
        first_screen_end("## Install\n")
    with pytest.raises(ReadmeError, match="found 2"):
        first_screen_end("## Quick Start\n## Quick Start\n")


def test_doc_test_plan_ignores_a_file_block_with_no_save_prose() -> None:
    md = f"Some illustrative code:\n\n{FENCE}python\nprint('not run')\n{FENCE}\n"
    steps, skips = doc_test_plan(doc_blocks(md))
    assert steps == [] and skips == []


def test_doc_test_plan_saves_a_named_file_block_then_runs_a_command_that_needs_it() -> None:
    md = (f"Save this as `sim.yaml`.\n\n{FENCE}yaml\nroot: {{}}\n{FENCE}\n\n"
          f"{FENCE}bash\nshal probe sim.yaml\n{FENCE}\n")
    steps, skips = doc_test_plan(doc_blocks(md))
    assert skips == []
    save, run = steps
    assert isinstance(save, Save) and save.name == "sim.yaml"
    assert isinstance(run, Run) and run.argv == ["shal", "probe", "sim.yaml"]


def test_doc_test_plan_skips_a_marked_block_and_reports_its_reason() -> None:
    md = (f"Needs a real bench.\n\n<!-- doc-test: skip needs hardware -->\n"
          f"{FENCE}bash\nshal probe bench.yaml\n{FENCE}\n")
    steps, skips = doc_test_plan(doc_blocks(md))
    assert steps == []
    assert skips == [(4, "needs hardware")]


def test_doc_test_plan_runs_a_main_only_skip_when_rc_wheels_is_on() -> None:
    """shal#361: a block skipped only because PyPI lacks the command runs once the
    venv installs the release-candidate wheel instead."""
    md = (f"Samples (main only, not in the PyPI release yet):\n\n"
          f"<!-- doc-test: skip {run_readme.MAIN_ONLY_MARK}; PyPI 0.3.0 lacks it -->\n"
          f"{FENCE}bash\nshal docs --samples\n{FENCE}\n")
    blocks = doc_blocks(md)

    steps, skips = doc_test_plan(blocks)
    assert steps == []
    assert skips == [(4, f"{run_readme.MAIN_ONLY_MARK}; PyPI 0.3.0 lacks it")]

    steps, skips = doc_test_plan(blocks, rc_wheels=True)
    assert skips == []
    [run] = steps
    assert isinstance(run, Run) and run.argv == ["shal", "docs", "--samples"]


def test_doc_test_plan_still_skips_a_non_main_only_block_when_rc_wheels_is_on() -> None:
    """A skip for a reason other than the PyPI-vs-main gap (shell syntax, hardware,
    secrets) is not unlocked by `rc_wheels` — a release-candidate wheel doesn't fix
    those."""
    md = (f"Needs a real bench.\n\n<!-- doc-test: skip needs hardware -->\n"
          f"{FENCE}bash\nshal probe bench.yaml\n{FENCE}\n")
    steps, skips = doc_test_plan(doc_blocks(md), rc_wheels=True)
    assert steps == []
    assert skips == [(4, "needs hardware")]


def test_doc_test_plan_exit_code_falls_back_to_the_paragraph_right_before_the_block() -> None:
    md = (f"`shal call` refuses a gated op with exit 2:\n\n"
          f"{FENCE}bash\nshal call sim.yaml ambient_temp set_target 30 --json\n{FENCE}\n\n"
          f"No more to say.\n")
    steps, skips = doc_test_plan(doc_blocks(md))
    assert skips == []
    [run] = steps
    assert isinstance(run, Run) and run.exit_code == 2


def test_doc_test_plan_exit_code_prefers_the_paragraph_right_after_the_block() -> None:
    md = (f"`shal call` refuses a gated op with exit 2:\n\n"
          f"{FENCE}bash\nshal probe sim.yaml\n{FENCE}\n\n"
          f"It exits 0 here, same as always.\n")
    steps, skips = doc_test_plan(doc_blocks(md))
    [run] = steps
    assert run.exit_code == 0


def test_rc_wheels_argv_substitutes_find_links_and_keeps_the_package_name() -> None:
    rc = Path("/tmp/rc-wheels")
    assert rc_wheels_argv(["pip", "install", "pyshal"], rc) == [
        "pip", "install", "--no-index", "--find-links", str(rc), "pyshal"]
    assert rc_wheels_argv(["pip", "install", "pyshal[mcp]"], rc) == [
        "pip", "install", "--no-index", "--find-links", str(rc), "pyshal[mcp]"]


def test_rc_wheels_argv_rejects_a_non_pip_install_line() -> None:
    with pytest.raises(ReadmeError, match="RC_WHEELS only applies to a `pip install` line"):
        rc_wheels_argv(["shal", "probe", "sim.yaml"], Path("/tmp/rc-wheels"))
