#!/usr/bin/env python
"""Run the README's Quick Start exactly as printed, in a clean venv (shal#159).

The README is the source. This script keeps no copy of the Quick Start: it
reads ``README.md``, finds the ``## Quick Start`` section (up to the next
``## `` heading; ``###`` subsections belong to it), and turns its fenced
blocks, in order, into steps:

- A shell block (```bash / sh / shell / console) holds commands. If any line
  starts with a ``$ `` prompt, the prompt lines are commands and the lines
  after each are its expected output. Otherwise every non-blank line is a
  command (a trailing ``# comment`` is dropped, as a shell would).
- An untagged (or ```text / output) block that comes straight after a shell
  block, with no prose between them, is the output the README shows for the
  last command of that block.
- A file block (```yaml / python / json / toml) is saved to disk under the
  name the prose right before it gives: "Save this as `NAME`". If the prose
  gives no name, that is a README defect and the run fails naming the block.
  The script never invents a name.

Commands run with ``subprocess`` from an argv (no shell, so the same on
bash and PowerShell), in a fresh temp dir, with the given venv's ``bin`` /
``Scripts`` dir first on ``PATH`` — so ``pip``, ``shal`` and ``python`` are
the venv's, as they are for a reader who activated it. Shell syntax the
runner does not interpret (``|``, ``&&``, ``>``, ``;``) fails the run
instead of being run half-right.

**The one substitution.** The README's ``pip install pyshal`` would test
PyPI, not this commit. So the README's pip line is run as printed, with the
package name ``pyshal`` replaced by the path of the wheel built from this
commit. Nothing else in any command is changed.

**Published mode (shal#188): no substitution at all.** With ``--published``
instead of ``--dist``, the README's pip line runs verbatim, so it installs
whatever PyPI serves today — what a reader actually downloads. That is the
only difference between the two modes. Right after the pip line passes, the
script reads the installed ``pyshal`` version from the venv's own metadata
(``importlib.metadata``) and prints it; the pass line, a failure line
("README Quick Start FAILED against published pyshal 0.2.2: ...") and the
``$GITHUB_STEP_SUMMARY`` entry all name that version.

**Pass/fail.** A step fails when its command exits with a code other than
the expected one, or when its output does not match the output the README
shows. The expected exit code is 0, unless the first paragraph after the
shown output says "exits N" (the README states the refusal of a gated write
"exits 2"). Output is compared line by line, exactly, ignoring only CRLF and
trailing whitespace. A command with no output block shown is checked on its
exit code only.

**The one loose match — drifting readings.** The simulated sensor drifts,
and the README says so, so a reading cannot match by equality. A shown
output line is treated as a reading only when its whole value is a decimal
number with a point: either ``label: 25.59`` (``label`` an identifier) or a
bare ``25.59``. Such a line matches any printed float in the same place with
the *same label*; every other line, including lines with integers like
``# 1 read(s)``, must match exactly.

**Time to first success.** Elapsed seconds from the start of the README's
``pip install`` to the end of the first command whose shown output holds a
reading and which passed. Printed, and appended to ``$GITHUB_STEP_SUMMARY``
when that is set. No such read in the Quick Start fails the run.

Usage:
    run_readme.py --venv VENV_DIR --dist DIST_DIR [--readme README.md]
    run_readme.py --venv VENV_DIR --published [--readme README.md]

``VENV_DIR`` is a clean venv (created by the caller, nothing installed).
``DIST_DIR`` must hold exactly one wheel. Exit 0 when every step passes,
1 otherwise, with the failing step and a diff on stdout.
"""
from __future__ import annotations

import argparse
import difflib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

SHELL_LANGS = {"bash", "sh", "shell", "console", "shell-session"}
OUTPUT_LANGS = {"", "text", "output"}
FILE_LANGS = {"yaml", "yml", "python", "py", "json", "toml"}
PACKAGE = "pyshal"

_SECTION = re.compile(r"^##\s+Quick Start\s*$")
_FENCE = re.compile(r"^```\s*([\w-]*)\s*$")
_SAVE_AS = re.compile(r"\bSave (?:this|it)(?: file)? as `([^`]+)`")
_EXITS = re.compile(r"\bexits (\d+)\b")
_SHELL_OPS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "2>", "2>&1"}
# A shown reading: `label: 25.59` or a bare `25.59` (decimal with a point).
_READING = re.compile(r"^(?P<label>(?:[A-Za-z_][\w.]*: )?)-?\d+\.\d+$")
# What a printed Python float looks like.
_FLOAT = r"-?(?:\d+\.\d+(?:[eE][+-]?\d+)?|\d+[eE][+-]?\d+|inf|nan)"


class ReadmeError(Exception):
    """The README's Quick Start cannot be run as printed."""


class StepFailed(Exception):
    """A Quick Start command failed or printed something other than the README shows."""


@dataclass
class Block:
    lang: str
    text: str
    line: int  # 1-based README line of the opening fence
    before: str = ""  # prose between the previous fence and this one
    after: str = ""  # prose between this fence and the next one


@dataclass
class Save:
    name: str
    content: str
    line: int


@dataclass
class Run:
    raw: str
    argv: list[str]
    line: int
    expected: list[str] | None = None
    exit_code: int = 0
    installs: bool = False  # the README's `pip install` (timer start)
    reads: bool = False  # its shown output holds a reading (timer stop)


@dataclass
class Installed:
    """What the README's pip line put in the venv (published mode only)."""
    version: str | None = None


def quickstart_blocks(readme: str) -> list[Block]:
    """Fenced blocks of the ``## Quick Start`` section, in order."""
    lines = readme.replace("\r\n", "\n").split("\n")
    starts = [i for i, ln in enumerate(lines) if _SECTION.match(ln)]
    if len(starts) != 1:
        raise ReadmeError(f"expected one '## Quick Start' heading, found {len(starts)}")
    blocks: list[Block] = []
    prose: list[str] = []
    fence: Block | None = None
    body: list[str] = []
    for i in range(starts[0] + 1, len(lines)):
        ln = lines[i]
        if fence is None:
            if ln.startswith("## "):
                break
            m = _FENCE.match(ln)
            if m:
                fence = Block(lang=m.group(1).lower(), text="", line=i + 1,
                              before="\n".join(prose).strip())
                if blocks:
                    blocks[-1].after = fence.before
                prose, body = [], []
            else:
                prose.append(ln)
        elif ln.strip() == "```":
            fence.text = "\n".join(body)
            blocks.append(fence)
            fence = None
        else:
            body.append(ln)
    if fence is not None:
        raise ReadmeError(f"README line {fence.line}: code fence never closed")
    if blocks:
        blocks[-1].after = "\n".join(prose).strip()
    return blocks


def _first_paragraph(text: str) -> str:
    return re.split(r"\n\s*\n", text.strip(), maxsplit=1)[0] if text.strip() else ""


def _argv(cmd: str, line: int) -> list[str]:
    argv = shlex.split(cmd, comments=True, posix=True)
    bad = [t for t in argv if t in _SHELL_OPS]
    if bad:
        raise ReadmeError(f"README line {line}: `{cmd}` uses shell syntax {bad} "
                          "this runner does not interpret")
    return argv


def plan(blocks: list[Block]) -> list[Save | Run]:
    """Turn the Quick Start blocks into ordered steps (files to save, commands to run)."""
    steps: list[Save | Run] = []
    last_shell: Block | None = None
    for b in blocks:
        if b.lang in SHELL_LANGS:
            runs: list[Run] = []
            prompted = any(ln.startswith("$ ") for ln in b.text.split("\n"))
            for ln in b.text.split("\n"):
                if prompted:
                    if ln.startswith("$ "):
                        runs.append(Run(raw=ln[2:], argv=_argv(ln[2:], b.line), line=b.line,
                                        expected=[]))
                    elif runs:
                        runs[-1].expected.append(ln)  # type: ignore[union-attr]
                    elif ln.strip():
                        raise ReadmeError(f"README line {b.line}: output before any `$ ` prompt")
                elif ln.strip() and not ln.lstrip().startswith("#"):
                    runs.append(Run(raw=ln.strip(), argv=_argv(ln, b.line), line=b.line))
            if not runs:
                raise ReadmeError(f"README line {b.line}: ```{b.lang} block has no command")
            runs[-1].exit_code = _exit_code(b.after)
            steps.extend(runs)
            last_shell = b
        elif b.lang in OUTPUT_LANGS:
            if last_shell is None or b.before or not isinstance(steps[-1], Run) \
                    or steps[-1].expected is not None:
                raise ReadmeError(f"README line {b.line}: output block does not follow a command")
            steps[-1].expected = b.text.split("\n")
            steps[-1].exit_code = _exit_code(b.after)
            last_shell = None
        elif b.lang in FILE_LANGS:
            m = _SAVE_AS.search(b.before)
            if not m:
                raise ReadmeError(
                    f"README line {b.line}: the ```{b.lang} block is never given a file name "
                    "(no \"Save this as `NAME`\" before it) — a reader cannot save it")
            steps.append(Save(name=m.group(1), content=b.text + "\n", line=b.line))
            last_shell = None
        else:
            raise ReadmeError(f"README line {b.line}: don't know how to run a ```{b.lang} block")
    for s in steps:
        if isinstance(s, Run):
            s.installs = _is_pip_install(s.argv)
            s.reads = any(_READING.match(ln.rstrip()) for ln in s.expected or [])
    return steps


def _exit_code(after: str) -> int:
    m = _EXITS.search(_first_paragraph(after))
    return int(m.group(1)) if m else 0


def _is_pip_install(argv: list[str]) -> bool:
    return len(argv) >= 2 and argv[0] == "pip" and argv[1] == "install"


def substitute_wheel(argv: list[str], wheel: Path) -> list[str]:
    """The README's pip line with ``pyshal`` replaced by the wheel's path (extras kept)."""
    out, hit = [], False
    for tok in argv:
        if tok == PACKAGE or tok.startswith(PACKAGE + "["):
            out.append(str(wheel) + tok[len(PACKAGE):])
            hit = True
        else:
            out.append(tok)
    if not hit:
        raise ReadmeError(f"the README's pip line `{shlex.join(argv)}` does not install {PACKAGE}")
    return out


def _norm(text: list[str] | str) -> list[str]:
    lines = text.replace("\r\n", "\n").split("\n") if isinstance(text, str) else list(text)
    lines = [ln.rstrip() for ln in lines]
    while lines and not lines[-1]:
        lines.pop()
    return lines


def output_matches(expected: list[str] | str, actual: str) -> bool:
    """Exact line-by-line match, except a shown reading matches any float, same label."""
    exp, act = _norm(expected), _norm(actual)
    if len(exp) != len(act):
        return False
    for e, a in zip(exp, act, strict=True):
        m = _READING.match(e)
        if m:
            if not re.fullmatch(re.escape(m.group("label")) + _FLOAT, a):
                return False
        elif e != a:
            return False
    return True


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def installed_version(venv: Path, env: dict[str, str]) -> str:
    """The ``pyshal`` version in the venv, from the venv's own package metadata."""
    py = shutil.which("python", path=str(venv_bin(venv)))
    if py is None:
        raise ReadmeError(f"no python in {venv_bin(venv)}")
    proc = subprocess.run(
        [py, "-c", f"from importlib.metadata import version; print(version({PACKAGE!r}))"],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    out = proc.stdout.decode("utf-8", errors="replace").strip()
    if proc.returncode != 0 or not out:
        raise StepFailed(f"the pip line passed but {PACKAGE} is not installed in the venv:\n{out}")
    return out


def run_quickstart(readme: Path, venv: Path, wheel: Path | None, workdir: Path,
                   installed: Installed | None = None) -> float:
    """Run every step; return seconds from pip install to the first good read.

    ``wheel=None`` is published mode: the pip line runs verbatim (PyPI), and the
    version it installed is stored in ``installed`` as soon as it is known.
    """
    steps = plan(quickstart_blocks(readme.read_text(encoding="utf-8")))
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["VIRTUAL_ENV"] = str(venv)
    env["PATH"] = str(venv_bin(venv)) + os.pathsep + env.get("PATH", "")
    env["PYTHONUNBUFFERED"] = "1"  # keep stdout/stderr in the order a terminal shows
    t0: float | None = None
    first_read: float | None = None
    for s in steps:
        if isinstance(s, Save):
            print(f"--- save {s.name} (README line {s.line})")
            (workdir / s.name).parent.mkdir(parents=True, exist_ok=True)
            (workdir / s.name).write_text(s.content, encoding="utf-8")
            continue
        swap = s.installs and wheel is not None
        argv = substitute_wheel(s.argv, wheel) if s.installs and wheel else list(s.argv)
        exe = shutil.which(argv[0], path=env["PATH"])
        if exe is None:
            raise ReadmeError(f"README line {s.line}: `{argv[0]}` not found on PATH")
        print(f"$ {s.raw}" + (f"    [run as: {shlex.join(argv)}]" if swap else ""))
        if s.installs and t0 is None:
            t0 = time.monotonic()
        proc = subprocess.run([exe, *argv[1:]], cwd=workdir, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=900)
        out = proc.stdout.decode("utf-8", errors="replace")
        print(out, end="" if out.endswith("\n") or not out else "\n")
        if proc.returncode != s.exit_code:
            raise StepFailed(f"README line {s.line}: `{s.raw}` exited {proc.returncode}, "
                             f"the README expects {s.exit_code}")
        if s.expected is not None and not output_matches(s.expected, out):
            diff = "\n".join(difflib.unified_diff(
                _norm(s.expected), _norm(out), "README shows", "command printed", lineterm=""))
            raise StepFailed(f"README line {s.line}: `{s.raw}` printed something other than "
                             f"the README shows\n{diff}")
        if s.installs and wheel is None and installed is not None and installed.version is None:
            installed.version = installed_version(venv, env)
            print(f"--- installed from PyPI: {PACKAGE} {installed.version}")
        if s.reads and first_read is None and t0 is not None:
            first_read = time.monotonic() - t0
    if first_read is None:
        raise StepFailed("the Quick Start never reaches a successful read after pip install")
    return first_read


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--venv", type=Path, required=True, help="a clean venv to install into")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--dist", type=Path, help="dir holding exactly one wheel")
    source.add_argument("--published", action="store_true",
                        help="run the README's pip line verbatim, against PyPI (#188)")
    ap.add_argument("--readme", type=Path,
                    default=Path(__file__).resolve().parents[2] / "README.md")
    args = ap.parse_args(argv)
    try:  # echo the commands' UTF-8 output (the probe prints an em dash) on any console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    if args.published:
        return _main_published(args.readme, args.venv)
    wheels = sorted(args.dist.glob("*.whl"))
    if len(wheels) != 1:
        print(f"FAIL: {args.dist} must hold exactly one wheel, found {len(wheels)}")
        return 1
    workdir = Path(tempfile.mkdtemp(prefix="shal-quickstart-"))
    print(f"Quick Start from {args.readme} in {workdir} (venv {args.venv})")
    try:
        secs = run_quickstart(args.readme, args.venv.resolve(), wheels[0].resolve(), workdir)
    except (ReadmeError, StepFailed) as e:
        print(f"FAIL: {e}")
        return 1
    line = f"README Quick Start passed: `pip install` to first successful read in {secs:.1f} s"
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"{line} ({sys.platform}).\n")
    return 0


def _main_published(readme: Path, venv: Path) -> int:
    """Published mode: the README as printed, pip line included; every result names the version."""
    workdir = Path(tempfile.mkdtemp(prefix="shal-quickstart-"))
    print(f"Quick Start from {readme} in {workdir} (venv {venv}), pip line verbatim (PyPI)")
    installed = Installed()
    try:
        secs = run_quickstart(readme, venv.resolve(), None, workdir, installed)
    except (ReadmeError, StepFailed) as e:
        what = f"published {PACKAGE} {installed.version or '(version unknown: not installed)'}"
        line, ok = f"README Quick Start FAILED against {what}: {e}", False
    else:
        line = (f"README Quick Start passed against published {PACKAGE} {installed.version}: "
                f"`pip install` to first successful read in {secs:.1f} s")
        ok = True
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:  # the first line only: a failing step's diff stays in the log
        head = line.split("\n", 1)[0]
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"{head} ({sys.platform}).\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
