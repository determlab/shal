"""examples/demos/virtual-bench/test_run_bench_record.py — issue #379 (p0).

`run_bench.py` must read the record **its own run** wrote, never
`records[-1]`: two runs that land in the same wall-clock second sort by
their random id suffix (`rec-<second>-<hex>`), not by write order, so
`records[-1]` can silently return a different run's record — the bug seen
as verdict `pass` printed with the DMM unplugged (shal#378, a macOS CI
cell).

This test pins both the timestamp (`started` ties on purpose) and the id
ordering (the PASS record's id is chosen to sort *after* the FAIL record's,
reproducing the exact ordering that fooled `records[-1]`), by stubbing
`subprocess.run` so no real pytest-shal process is needed — that package
isn't on PyPI and needs a network fetch per the README (the full real-stack
version of this scenario is out of this demo's scope; see
`tests/test_virtual_bench_demo.py`). Only `subprocess.run` is replaced:
`run_bench.main()` itself, including the record-selection code under test,
runs exactly as shipped.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent

_spec = importlib.util.spec_from_file_location("run_bench", HERE / "run_bench.py")
assert _spec is not None and _spec.loader is not None
run_bench = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_bench)

from shal import record as shal_record  # noqa: E402 — after sys.path-free module load above

SAME_SECOND = "2026-01-01T00:00:00Z"
# `started` ties (same second), so `records[-1]` (sorted by `(started, record)`)
# breaks the tie on the id string. Chosen so the PASS run's id sorts *after*
# the FAIL run's — the ordering that reproduces the bug.
PASS_RECORD_ID = "rec-same-zzzzzz"
FAIL_RECORD_ID = "rec-same-aaaaaa"
assert sorted([PASS_RECORD_ID, FAIL_RECORD_ID])[-1] == PASS_RECORD_ID


def _write_record(store: Path, *, record_id: str, verdict: str) -> None:
    if verdict == "pass":
        steps: tuple[shal_record.Step, ...] = ()
    elif verdict == "fail":
        steps = (shal_record.Step(name="test_bench_output_is_3v3", verdict="fail"),)
    else:
        raise ValueError(verdict)
    rec = shal_record.Record(
        record=record_id,
        unit="bench",
        station="bench.yaml",
        sequence="test_bench.py::test_bench_output_is_3v3",
        sequence_version="0" * 40,
        setup="bench.yaml",
        setup_version="0" * 40,
        runner="pytest",
        started=SAME_SECOND,
        ended=SAME_SECOND,
        steps=steps,
    )
    shal_record.write(rec, store)


def test_a_fail_run_right_after_a_pass_run_in_the_same_second_reads_its_own_record(
    tmp_path, monkeypatch, capsys,
):
    monkeypatch.setattr(run_bench, "HERE", tmp_path)

    plan = iter([(PASS_RECORD_ID, "pass"), (FAIL_RECORD_ID, "fail")])

    def fake_pytest_run(*args, **kwargs):
        record_id, verdict = next(plan)
        _write_record(tmp_path, record_id=record_id, verdict=verdict)
        return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0,
                                            stdout="", stderr="")

    monkeypatch.setattr(run_bench.subprocess, "run", fake_pytest_run)

    code1 = run_bench.main([])
    summary1 = json.loads(capsys.readouterr().out)
    assert code1 == run_bench.EXIT_PASS
    assert summary1["record"] == PASS_RECORD_ID
    assert summary1["verdict"] == "pass"

    # The real bug: a second run, landing in the same second, that wrote a
    # FAIL record must never report the PASS record it happens to share a
    # store (and a `started` second) with.
    code2 = run_bench.main([])
    summary2 = json.loads(capsys.readouterr().out)
    assert summary2["record"] == FAIL_RECORD_ID, (
        f"run_bench read {summary2['record']!r}, not this run's own "
        f"{FAIL_RECORD_ID!r} — it is reading by position, not by run"
    )
    assert summary2["verdict"] == "fail"
    assert code2 == run_bench.EXIT_FAIL
