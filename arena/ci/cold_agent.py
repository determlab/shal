"""Cold-agent measurement for the arena (issue #317): docs only, clean venv, timed.

    python arena/ci/cold_agent.py --dist DIST --venv DIR [--limit-s 600]

From a folder with nothing installed, a scripted agent follows the docs to
first success, and the time is printed:

    cold-agent: first success in 41.3 s (limit 600 s)

It exits 1 over the limit, or when first success never comes. "First success
in ten minutes" is claimed only while this passes.

**What the agent reads.** The install line is the one in AGENTS.md's first
code block ("Fastest start"), parsed from the file, not copied here. Every
``shal-arena`` subcommand it runs (`SUBCOMMANDS`) must be printed in
arena/README.md (a test pins that). Its driver is the README's minimal
example for each instrument's case. Everything else comes from the run's own
JSON. The play itself is `two_agents.solve`, through the CLI, using the
``shal-arena`` console script inside the clean venv.

**First success** is a closed run on ``rail-3v3`` (``--seed 1``, the seed the
README's walkthrough uses) that is not disqualified and wrote its score file.
``shal-arena ui``, the last line of the fastest start, serves a page until
stopped, so it cannot be the finish line of a timed job.

**The one substitution.** The docs install pyshal and shal-arena from the
``main`` branch on GitHub. That would time ``main``, not this commit. So
each of the two git URLs is replaced by the matching wheel built from this
commit (``--dist``); any other token in the line fails the run, so a changed
install line is noticed here. Dependencies (pyyaml, jsonschema) still come
from the package index, as they do for a reader. ``pip install pyshal``
from PyPI cannot be the line: PyPI has pyshal 0.3.0 and shal-arena needs
0.4.0 or later (AGENTS.md).

The clock starts before ``python -m venv`` and stops when ``answer`` returns.
"""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import two_agents  # noqa: E402

_ROOT = _HERE.parents[1]
AGENTS_MD = _ROOT / "AGENTS.md"
LIMIT_S = 600.0
#: the shal-arena subcommands the play runs, in order
SUBCOMMANDS = ("tasks", "run", "drive", "check-driver", "measure", "answer")
#: the git URLs in the docs' install line -> the wheel that stands in for each
_URL_TO_WHEEL = {
    "git+https://github.com/determlab/shal": "pyshal-*.whl",
    "git+https://github.com/determlab/shal#subdirectory=arena": "shal_arena-*.whl",
}
_FENCE_RE = re.compile(r"```\w*\n(.*?)```", re.DOTALL)


class StepFailed(Exception):
    """A step did not reach first success."""


def install_line(agents_md: str) -> str:
    """The one ``pip install`` line in AGENTS.md's first code block."""
    m = _FENCE_RE.search(agents_md)
    lines = [ln.strip() for ln in (m.group(1) if m else "").splitlines()
             if ln.strip().startswith("pip install")]
    if len(lines) != 1:
        raise StepFailed(f"AGENTS.md's first code block must hold one pip install line, "
                         f"found {lines}")
    return lines[0]


def install_args(line: str, dist: Path) -> list[str]:
    """The install line's package arguments, each git URL swapped for this
    commit's wheel."""
    tokens = shlex.split(line)
    if tokens[:2] != ["pip", "install"] or len(tokens) < 3:
        raise StepFailed(f"not a pip install line: {line!r}")
    args = []
    for token in tokens[2:]:
        pattern = _URL_TO_WHEEL.get(token)
        if pattern is None:
            raise StepFailed(f"install line has {token!r}, which this job cannot map to a "
                             f"wheel from this commit; known: {', '.join(_URL_TO_WHEEL)}")
        wheels = sorted(Path(dist).glob(pattern))
        if len(wheels) != 1:
            raise StepFailed(f"expected one {pattern} in {dist}, found {wheels}")
        args.append(str(wheels[0]))
    return args


def timed(steps: Callable[[], object], limit_s: float, *,
          out: Callable[[str], None] = print) -> int:
    """Run ``steps``, print the measured time, exit 1 over the limit or on
    a failed step."""
    start = time.perf_counter()
    try:
        steps()
    except StepFailed as e:
        elapsed = time.perf_counter() - start
        line = (f"cold-agent: no first success after {elapsed:.1f} s "
                f"(limit {limit_s:g} s): {e}")
        out(line)
        _summary(line)
        return 1
    elapsed = time.perf_counter() - start
    over = elapsed > limit_s
    line = (f"cold-agent: first success in {elapsed:.1f} s (limit {limit_s:g} s)"
            + (" -- FAIL: over the limit" if over else ""))
    out(line)
    _summary(line)
    return 1 if over else 0


def _summary(line: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _run(argv: list[str], **kw: object) -> None:
    print("cold-agent: $ " + " ".join(argv), flush=True)
    proc = subprocess.run(argv, stdin=subprocess.DEVNULL, **kw)  # type: ignore[call-overload]
    if proc.returncode != 0:
        raise StepFailed(f"{Path(argv[0]).name} {' '.join(argv[1:3])} exited {proc.returncode}")


def first_success(venv: Path, dist: Path, workdir: Path) -> dict:
    """Clean venv, the docs' install line, then one task to a closed run."""
    if venv.exists():
        raise StepFailed(f"{venv} already exists; a cold start needs a new folder")
    _run([sys.executable, "-m", "venv", str(venv)])
    py = _venv_bin(venv) / ("python.exe" if os.name == "nt" else "python")
    line = install_line(AGENTS_MD.read_text(encoding="utf-8"))
    _run([str(py), "-m", "pip", "install", "--disable-pip-version-check",
          *install_args(line, dist)])
    exe = shutil.which("shal-arena", path=str(_venv_bin(venv)))
    if exe is None:
        raise StepFailed(f"the install did not put `shal-arena` in {_venv_bin(venv)}")
    workdir.mkdir(parents=True, exist_ok=True)
    call = two_agents.cli_caller(workdir / ".shal-arena", prefix=[exe])
    try:
        result = two_agents.solve(call, workdir)
    except two_agents.StepFailed as e:
        raise StepFailed(str(e)) from e
    if result["disqualified"]:
        raise StepFailed(f"run {result['run_id']} closed but was disqualified")
    score = workdir / ".shal-arena" / f"{result['run_id']}.score.json"
    if not score.is_file():
        raise StepFailed(f"answer wrote no score file at {score}")
    print(f"cold-agent: run {result['run_id']} closed, answered {result['given']!r}, "
          f"score file {score}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cold_agent.py", description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, required=True,
                        help="folder holding this commit's pyshal and shal_arena wheels")
    parser.add_argument("--venv", type=Path, required=True,
                        help="a folder that does not exist yet; the clean venv goes here")
    parser.add_argument("--workdir", type=Path, default=None,
                        help="where the agent writes its drivers and run state "
                             "(default: a new temp folder)")
    parser.add_argument("--limit-s", type=float, default=LIMIT_S,
                        help=f"fail over this many seconds (default {LIMIT_S:g})")
    args = parser.parse_args(argv)
    workdir = args.workdir or Path(tempfile.mkdtemp(prefix="cold-agent-"))
    return timed(lambda: first_success(args.venv.resolve(), args.dist.resolve(),
                                       workdir.resolve()), args.limit_s)


if __name__ == "__main__":
    sys.exit(main())
