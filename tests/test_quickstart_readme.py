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

from run_readme import (  # noqa: E402
    ReadmeError,
    Run,
    Save,
    output_matches,
    plan,
    quickstart_blocks,
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
         "# writes — not run by --probe: ambient_temp__set_target"]


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

