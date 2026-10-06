#!/usr/bin/env python3
"""The virtual bench, one command (issue #305, T8): a PSU sets 3.3 V, a DMM
measures it, pytest-shal writes one record. ``--unplug <node id>`` (or
``$SHAL_SIM_UNPLUG``) breaks that device's link first — the run reads back
"no answer from the instrument", not a failed measurement.

Exit codes follow ``shal call``'s own vocabulary (AGENTS.md): 0 the bench
passed, 1 a check failed, 3 the run could not happen at all, 4 the probed
instrument never answered (same code as shal#300's ``Unreachable``).

pytest-shal main (pinned by the README) builds the error ``Step`` through
``shal.record.Step.from_error``, so the stored record (``records.db`` /
``records/*.yaml`` beside this file) carries ``cause: "transport"`` for this
bench's dead-link run, not ``cause: null`` (an older pin, e240b07, predated
that and left the stored record's ``cause`` null — shal#301 landed in pyshal
after that plugin code was written). This bench has exactly one way to
error — the probed instrument never answers — so this script still derives
``cause`` itself for the one line of JSON it prints below, rather than
reading the record back.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_CANNOT_RUN = 3
EXIT_UNREACHABLE = 4

# pytest-shal isn't on PyPI (README's Install section), so the fix for a pytest
# subprocess that never ran (no module, or an unrecognized --shal-setup flag) is
# this pinned source, not a bare package name.
PYTEST_SHAL_FIX = ('pip install pytest==9.1.1 "pytest-shal @ '
                   'git+https://github.com/determlab/pytest-shal'
                   '@f45937de74737473e3b2b896b25bef087468da40"')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unplug", metavar="ID", default=None,
                         help="fault-inject this node id as unreachable "
                              "(sets SHAL_SIM_UNPLUG for the run)")
    args = parser.parse_args(argv)

    env = dict(os.environ)
    if args.unplug:
        env["SHAL_SIM_UNPLUG"] = args.unplug
    unplugged = bool(env.get("SHAL_SIM_UNPLUG"))

    import shal
    from shal import record as shal_record

    if args.unplug:
        # issue #417: validate before spawning pytest, with the exact same
        # check (so the exact same error text) `shal.load()` itself makes --
        # an id that unplugs nothing must never silently pass. Set the env
        # var in THIS process only for the one load call, then restore it,
        # so the later `subprocess.run(..., env=env)` below is unaffected.
        prev_unplug = os.environ.get("SHAL_SIM_UNPLUG")
        os.environ["SHAL_SIM_UNPLUG"] = args.unplug
        try:
            shal.load(str(HERE / "bench.yaml")).close()
        except shal.LoadError as e:
            print(json.dumps({"ok": False, "error": str(e)}))
            return EXIT_CANNOT_RUN
        finally:
            if prev_unplug is None:
                os.environ.pop("SHAL_SIM_UNPLUG", None)
            else:
                os.environ["SHAL_SIM_UNPLUG"] = prev_unplug

    try:
        before = {r.record for r in shal_record.read(HERE)}
    except shal.Error as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return EXIT_CANNOT_RUN

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_bench.py",
         "--shal-setup", "bench.yaml", "-q", "--tb=line"],
        cwd=HERE, env=env, capture_output=True, text=True,
    )
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    try:
        # No `sequence` filter: pytest's nodeid (and so the stored `sequence`)
        # is relative to whatever rootdir pytest finds by walking up from
        # here, which differs between a standalone copy of this folder and a
        # checkout of the whole shal repo — this folder's store holds only
        # this one test's records anyway, so filtering on sequence buys nothing.
        records = shal_record.read(HERE)
    except shal.Error as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return EXIT_CANNOT_RUN

    # Never by position (#379): two runs that land in the same wall-clock
    # second sort by their random id suffix, not by write order, so taking
    # the last item of this list can silently return a different run's
    # record — seen as a verdict `pass` printed with the DMM unplugged. The
    # record THIS run wrote is whichever id did not exist in the store
    # before this run started.
    new = [r for r in records if r.record not in before]
    if not new:
        print(json.dumps({"ok": False,
                           "error": "no record written — see stderr for the pytest run",
                           "fix": PYTEST_SHAL_FIX}))
        return EXIT_CANNOT_RUN
    if len(new) > 1:
        ids = ", ".join(sorted(r.record for r in new))
        print(json.dumps({"ok": False,
                           "error": f"this run wrote more than one new record ({ids}) "
                                    f"— cannot tell which one is ours"}))
        return EXIT_CANNOT_RUN

    rec = new[0]
    cause = "transport" if (rec.verdict == "error" and unplugged) else None
    summary = {"verdict": rec.verdict, "cause": cause, "record": rec.record,
               "unit": rec.unit, "sequence": rec.sequence}
    print(json.dumps(summary))

    if rec.verdict == "pass":
        return EXIT_PASS
    if cause == "transport":
        return EXIT_UNREACHABLE
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
