#!/usr/bin/env python
"""Run every shipped sample the way a person runs it, and fail on a bad exit (shal#206).

A sample is for a person deciding whether SHAL does their job. One that no
longer runs from a fresh install tells them no. So this script runs them all.

**The set is listed, never named.** It asks the installed ``shal`` for the list:
``shal docs --samples --json``. That command lists every subfolder of the
installed ``shal/samples`` holding a ``run.py``, so a new sample is run without
editing this script or CI.

**Run the way a person runs it.** For each sample: ``shal docs --sample <name>
--to <new folder>``, then the one command that printed on stdout, run through
the shell from a different scratch folder, with stdin an **empty pipe** (a
sample that reads input gets EOF at once, and nothing hangs CI). Not
``DEVNULL``: on Windows that is the ``NUL`` device, and ``isatty()`` is True
for it, so shal's ``ConsoleApprover`` would believe a person is there, prompt,
read EOF and deny with the generic reason. A pipe is no terminal on any OS, so
a sample sees what a person's non-interactive run sees: no approver is set
and stdin is not a terminal. The venv's
``bin`` / ``Scripts`` dir is first on ``PATH``, so ``python`` and ``shal`` are the
venv's.

**Pass/fail.** A sample passes when it exits 0. A sample that must end another
way says so in an ``expect.json`` file in its folder (CI reads it; ``--to`` does
not write it, and ``--sample`` does not print it). Every key is optional::

    {"exit": 2,                        # the exit code (default 0)
     "stdout_has":   ["..."],          # each text must be in stdout
     "stdout_lacks": ["..."],          # each text must not be in stdout
     "stderr_has":   ["..."],
     "stderr_lacks": ["Traceback"]}

An unknown key is a failure, so a typo cannot pass silently. Each sample gets
one line (``ok`` / ``FAIL``), with what is wrong under a failing one. Exit 0
when every sample passes, 1 otherwise (no sample found is a failure too).

Usage:
    run_samples.py [--venv VENV_DIR] [--scratch DIR]

``VENV_DIR`` has the package installed (CI: a clean venv with the wheel built
from this commit); without it, the ``shal`` next to the running Python is used.
``--scratch`` is where the samples are written (default: a new temp folder).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXPECT_FILE = "expect.json"
_LISTS = ("stdout_has", "stdout_lacks", "stderr_has", "stderr_lacks")


class BadSample(Exception):
    """A sample cannot be written or run as a person would."""


def load_expect(folder: Path) -> dict:
    """The sample's ``expect.json``, checked; ``{"exit": 0}`` when there is none."""
    path = folder / EXPECT_FILE
    if not path.is_file():
        return {"exit": 0}
    try:
        expect = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BadSample(f"{path}: not JSON: {e}") from e
    if not isinstance(expect, dict):
        raise BadSample(f"{path}: must be a JSON object")
    unknown = sorted(set(expect) - {"exit", *_LISTS})
    if unknown:
        raise BadSample(f"{path}: unknown key(s) {unknown}; known: exit, {', '.join(_LISTS)}")
    if not isinstance(expect.get("exit", 0), int) or isinstance(expect.get("exit"), bool):
        raise BadSample(f"{path}: 'exit' must be an integer")
    for key in _LISTS:
        val = expect.get(key, [])
        if not (isinstance(val, list) and all(isinstance(s, str) for s in val)):
            raise BadSample(f"{path}: '{key}' must be a list of strings")
    return {"exit": 0, **expect}


def verdict(expect: dict, returncode: int, stdout: str, stderr: str) -> list[str]:
    """What is wrong with one run against its expectation; empty when it passes."""
    wrong = []
    want = expect.get("exit", 0)
    if returncode != want:
        wrong.append(f"exit {returncode}, expected {want}")
    streams = {"stdout": stdout, "stderr": stderr}
    for key in _LISTS:
        stream, mode = key.split("_")
        for text in expect.get(key, []):
            present = text in streams[stream]
            if mode == "has" and not present:
                wrong.append(f"{stream} lacks {text!r}")
            if mode == "lacks" and present:
                wrong.append(f"{stream} has {text!r}")
    return wrong


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def _tail(text: str, lines: int = 15) -> list[str]:
    return text.rstrip().splitlines()[-lines:]


def run_one(sample: dict, shal: str, scratch: Path, env: dict[str, str]) -> list[str]:
    """Write one sample with ``--to``, run the printed command; return what is wrong."""
    name = sample["name"]
    dest = scratch / "samples" / name
    expect = load_expect(Path(sample["folder"]))
    w = subprocess.run([shal, "docs", "--sample", name, "--to", str(dest)], env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    if w.returncode != 0:
        raise BadSample(f"--to exited {w.returncode}: {w.stderr.strip()}")
    cmd = w.stdout.strip()
    if not cmd or "\n" in cmd:
        raise BadSample(f"--to must print one command on stdout, printed {w.stdout!r}")
    written = sorted(p.name for p in dest.iterdir())
    if written != sorted(sample["files"]):
        raise BadSample(f"--to wrote {written}, --samples --json lists {sample['files']}")
    cwd = scratch / "cwd" / name  # not the sample's folder: it must run from anywhere
    cwd.mkdir(parents=True)
    print(f"      $ {cmd}")
    # input="": an empty PIPE, not DEVNULL. On Windows DEVNULL is the NUL device,
    # which isatty() calls a terminal, so shal's ConsoleApprover would think a
    # person is there, prompt, and read EOF (see the module docstring)
    r = subprocess.run(cmd, shell=True, cwd=cwd, env=env, input="",
                       capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=300)
    wrong = verdict(expect, r.returncode, r.stdout, r.stderr)
    if wrong:
        wrong += [f"stdout| {ln}" for ln in _tail(r.stdout)]
        wrong += [f"stderr| {ln}" for ln in _tail(r.stderr)]
    return wrong


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--venv", type=Path, help="a venv with the package installed")
    ap.add_argument("--scratch", type=Path, help="where to write the samples (default: temp)")
    args = ap.parse_args(argv)
    try:  # the samples' output may hold non-ASCII (em dashes) on any console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    bindir = venv_bin(args.venv.resolve()) if args.venv else Path(sys.executable).parent
    if args.venv:
        env["VIRTUAL_ENV"] = str(args.venv.resolve())
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    shal = shutil.which("shal", path=str(bindir)) or shutil.which("shal", path=env["PATH"])
    if shal is None:
        print(f"FAIL: no `shal` in {bindir} or on PATH")
        return 1
    listing = subprocess.run([shal, "docs", "--samples", "--json"], env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=60)
    try:
        samples = json.loads(listing.stdout)["samples"]
    except (json.JSONDecodeError, KeyError, TypeError):
        print(f"FAIL: `shal docs --samples --json` exited {listing.returncode} with no "
              f"sample list: {(listing.stderr or listing.stdout).strip()}")
        return 1
    scratch = (args.scratch.resolve() if args.scratch
               else Path(tempfile.mkdtemp(prefix="shal-samples-")))
    print(f"{len(samples)} sample(s) from {shal}, written under {scratch}")
    if not samples:
        print("FAIL: no sample found (a subfolder of shal/samples with a run.py)")
        return 1
    failed = []
    for sample in samples:
        print(f"-- {sample['name']}")
        try:
            wrong = run_one(sample, shal, scratch, env)
        except (BadSample, OSError, subprocess.TimeoutExpired) as e:
            wrong = [str(e)]
        print(f"{'FAIL' if wrong else 'ok  '}  {sample['name']}")
        for w in wrong:
            print(f"        {w}")
        if wrong:
            failed.append(sample["name"])
    if failed:
        print(f"FAIL: {len(failed)} of {len(samples)} sample(s): {', '.join(failed)}")
        return 1
    print(f"all {len(samples)} sample(s) ran as expected")
    return 0


if __name__ == "__main__":
    sys.exit(main())
