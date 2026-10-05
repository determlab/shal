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

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "test_bench.py",
         "--shal-setup", "bench.yaml", "-q", "--tb=line"],
        cwd=HERE, env=env, capture_output=True, text=True,
    )
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)

    import shal
    from shal import record as shal_record

    try:
        # No `sequence` filter: pytest's nodeid (and so the stored `sequence`)
        # is relative to whatever rootdir pytest finds by walking up from
        # here, which differs between a standalone copy of this folder and a
        # checkout of the whole shal repo — this folder's store holds only
        # this one test's records anyway, so the newest one is unambiguous.
        records = shal_record.read(HERE)
    except shal.Error as e:
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}))
        return EXIT_CANNOT_RUN
    if not records:
        print(json.dumps({"ok": False,
                           "error": "no record written — see stderr for the pytest run"}))
        return EXIT_CANNOT_RUN

    rec = records[-1]  # shal_record.read() returns oldest first; ours is the newest
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
