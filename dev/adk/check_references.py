#!/usr/bin/env python
"""Run ``shal check --json`` on every ADK reference and fail on any warning (shal#153).

A reference that quietly gains a warning teaches the wrong pattern to every
cold agent that copies it (ADK §3.6, R7). So this script checks the whole set
and fails on a non-empty ``problems`` **or** ``warnings`` list — stricter than
``shal check`` itself, whose exit code counts only problems.

**The set is listed, never named.** Every subfolder of the reference root that
holds a ``driver.py`` is a reference; a seventh one is checked without editing
this script or CI. The root is, by default, the ``shal/adk/reference`` folder
*installed* in the venv — what the wheel ships, not the source tree.

**Run the way an operator runs it.** For each folder, from that folder as cwd:
``shal check driver:<Class> --topology topology.yaml --json``, with the venv's
``bin`` / ``Scripts`` dir first on ``PATH``. ``<Class>`` is read from
``driver.py`` without importing it: the one class decorated with
``@registry.register`` (or ``@shal.register``, ``@register``) whose bases name ``Driver``. No
``driver.py`` with exactly one such class is a failure naming the file. A
reference needs no ``sim.py`` (sqlite's twin is its address).

**Pass/fail.** A reference fails when ``shal check`` exits 2 (the check could
not run), prints no JSON report, or reports any problem or warning. Each
reference gets one line (``ok`` / ``FAIL``), with every problem and warning
under a failing one. Exit 0 when every reference is clean, 1 otherwise.

Usage:
    check_references.py [--venv VENV_DIR] [--root REFERENCE_DIR]

``VENV_DIR`` has the package installed (CI: a clean venv with the wheel built
from this commit); without it, the ``shal`` next to the running Python is used.
``--root`` points at another reference folder — e.g. a copy with a
deliberately removed ``side_effect=``, to see the script fail.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


class BadReference(Exception):
    """A reference folder cannot be checked as an operator would check it."""


def list_references(root: Path) -> list[Path]:
    """Every subfolder of ``root`` holding a ``driver.py``, sorted by name."""
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "driver.py").is_file())


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return node.id if isinstance(node, ast.Name) else ""


def driver_class(driver_py: Path) -> str:
    """The name of the one registered ``Driver`` subclass in ``driver_py``."""
    tree = ast.parse(driver_py.read_text(encoding="utf-8"), filename=str(driver_py))
    names = [n.name for n in tree.body if isinstance(n, ast.ClassDef)
             and any(_dotted(d).split(".")[-1] == "register" for d in n.decorator_list)
             and any(_dotted(b).split(".")[-1] == "Driver" for b in n.bases)]
    if len(names) != 1:
        raise BadReference(f"{driver_py}: expected one @registry.register Driver "
                           f"subclass, found {len(names)} {names}")
    return names[0]


def verdict(report: dict) -> list[str]:
    """What is wrong with one ``shal check --json`` report; empty when it is clean."""
    return ([f"problem: {p}" for p in report.get("problems") or []]
            + [f"warning: {w}" for w in report.get("warnings") or []])


def venv_bin(venv: Path) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin")


def installed_root(python: str, env: dict[str, str]) -> Path:
    """The ``shal/adk/reference`` folder installed for ``python``."""
    code = ("from importlib.resources import files; "
            "print(files('shal') / 'adk' / 'reference')")
    out = subprocess.run([python, "-c", code], env=env, capture_output=True, text=True,
                         check=True).stdout
    return Path(out.strip())


def check_one(ref: Path, shal: str, env: dict[str, str]) -> tuple[str, list[str]]:
    """Run the check in ``ref``; return the ``module:Class`` and what is wrong."""
    target = f"driver:{driver_class(ref / 'driver.py')}"
    if not (ref / "topology.yaml").is_file():
        return target, [f"no topology.yaml in {ref}"]
    proc = subprocess.run([shal, "check", target, "--topology", "topology.yaml", "--json"],
                          cwd=ref, env=env, capture_output=True, text=True, timeout=300)
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return target, [f"shal check exited {proc.returncode} with no JSON report: "
                        f"{(proc.stderr or proc.stdout).strip()}"]
    return target, verdict(report)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--venv", type=Path, help="a venv with the package installed")
    ap.add_argument("--root", type=Path,
                    help="the reference folder (default: the one installed in the venv)")
    args = ap.parse_args(argv)
    try:  # the checks' messages may hold non-ASCII (em dashes) on any console
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"  # leave the checked folders as they were
    bindir = venv_bin(args.venv.resolve()) if args.venv else Path(sys.executable).parent
    if args.venv:
        env["VIRTUAL_ENV"] = str(args.venv.resolve())
    env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
    shal = shutil.which("shal", path=env["PATH"])
    python = shutil.which("python", path=str(bindir)) or sys.executable
    if shal is None:
        print(f"FAIL: no `shal` in {bindir} or on PATH")
        return 1
    root = args.root.resolve() if args.root else installed_root(python, env)
    refs = list_references(root)
    print(f"shal check on {len(refs)} reference(s) in {root} ({shal})")
    if not refs:
        print("FAIL: no reference folder (a subfolder with driver.py) found")
        return 1
    failed = []
    for ref in refs:
        try:
            target, wrong = check_one(ref, shal, env)
        except (BadReference, SyntaxError, subprocess.TimeoutExpired) as e:
            target, wrong = "?", [str(e)]
        print(f"{'FAIL' if wrong else 'ok  '}  {ref.name:<16} {target}")
        for w in wrong:
            print(f"        {w}")
        if wrong:
            failed.append(ref.name)
    if failed:
        print(f"FAIL: {len(failed)} of {len(refs)} reference(s) not clean: {', '.join(failed)}")
        return 1
    print(f"all {len(refs)} reference(s) clean: no problems, no warnings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
