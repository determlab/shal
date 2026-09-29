"""Every inline `shal <sub> ...` in the driver/bus/yaml skills parses (issue #283).

Parse only, nothing runs: the CLI parser is captured from ``cli.main`` and each
backticked command is fed to it. A skill that names a subcommand or flag the CLI
does not have fails here.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from shal import cli

SKILLS = Path(__file__).resolve().parent.parent / "integrations" / "claude-code" / "skills"
NAMES = ("shal-generate-driver", "shal-build-bus", "shal-build-yaml")

_FENCE = re.compile(r"```.*?```", re.S)
_INLINE = re.compile(r"`(shal [^`]+)`")
_PLACEHOLDER = re.compile(r"<[^<>\s]+>")


class _Captured(Exception):
    pass


def _parser() -> argparse.ArgumentParser:
    """The real CLI parser: run main() with parse_known_args stopped at the top."""
    got: list[argparse.ArgumentParser] = []
    orig = argparse.ArgumentParser.parse_known_args

    def grab(self, *a, **kw):
        if self.prog == "shal":
            got.append(self)
            raise _Captured
        return orig(self, *a, **kw)

    argparse.ArgumentParser.parse_known_args = grab  # type: ignore[method-assign]
    try:
        cli.main([])
    except _Captured:
        pass
    finally:
        argparse.ArgumentParser.parse_known_args = orig  # type: ignore[method-assign]
    return got[0]


def _commands() -> list[tuple[str, str]]:
    found = []
    for name in NAMES:
        text = _FENCE.sub("", (SKILLS / name / "SKILL.md").read_text(encoding="utf-8"))
        for m in _INLINE.finditer(text):
            found.append((name, " ".join(m.group(1).split())))
    return found


def _argv(cmd: str) -> list[str]:
    cmd = _PLACEHOLDER.sub("dummy", cmd)
    cmd = cmd.replace("...", "t.yaml node op")  # `shal call ... --via x`
    return cmd.split()[1:]


COMMANDS = _commands()


def test_finds_a_reasonable_number_of_commands():
    assert len(COMMANDS) >= 5, COMMANDS
    assert {n for n, _ in COMMANDS} == set(NAMES), COMMANDS
    assert any(c.startswith("shal check ") for _, c in COMMANDS)


def test_step5_check_certifies_the_sht31_example():
    """Step 5's call, run for real on the generated SHT31 example: exit 0."""
    gen = Path(__file__).resolve().parent.parent / "examples/driver-creator/sht31/generated"
    src = str(Path(cli.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": src}
    r = subprocess.run(
        [sys.executable, "-m", "shal.cli", "check", "driver:Sht31",
         "--topology", "topology.yaml", "--json"],
        cwd=gen, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("skill,cmd", COMMANDS)
def test_skill_command_parses(skill, cmd):
    argv = _argv(cmd)
    try:
        _, extra = _parser().parse_known_args(argv)
    except SystemExit as e:
        pytest.fail(f"{skill}: `{cmd}` rejected by the shal parser (exit {e.code})")
    assert not extra, f"{skill}: `{cmd}` has unknown arguments {extra}"
