"""issue #397: `bench` has a built-in default policy, so `shal-arena bench
--runs 10` works with one command and no `--policy` file -- the Done-when
runs this exact CLI form, non-interactively, through a subprocess (same
style `test_readme_commands.py` already uses). CTO review (round 2):
it must actually drive the card (on purpose, at a voltage the gate refuses
but raw SCPI does not -- real signal, not 0 vs 0), answer from the reading
rather than a hardcoded guess, and never leak its own driver registration
into a `--policy` run sharing the same process."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

_TIMEOUT = 60


def test_bench_runs_10_with_no_policy_and_no_task(tmp_path: Path) -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "shal_arena.cli", "bench", "--runs", "10", "--json"],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True, text=True,
        timeout=_TIMEOUT)
    assert proc.returncode == 0, proc.stderr
    doc = json.loads(proc.stdout)
    assert doc["ok"] is True
    assert doc["with_shal"]["runs"] == 10
    assert doc["without_shal"]["runs"] == 10
    # the actual point of driving on purpose (CTO review): shal's own drive
    # gate protects the card every time; raw SCPI has no gate and destroys
    # it on every run it reaches an answer for -- real, comparable signal,
    # not 0 vs 0. (A run whose probe hits the `open` fault never reaches its
    # own `destroyed` check -- issue #395's own synthetic result for that
    # case reports `destroyed: False` regardless -- so this checks "more
    # than zero", not "every run".)
    assert doc["with_shal"]["destroyed"] == 0
    assert doc["without_shal"]["destroyed"] > 0


def test_bench_default_policy_is_importable_from_python(tmp_path: Path) -> None:
    from shal_arena.bench import (
        DEFAULT_POLICY,
        DEFAULT_TASK,
        MIN_RUNS,
        _default_dmm_driver_registered,
        run_benchmark,
    )

    with _default_dmm_driver_registered():
        result = run_benchmark(str(DEFAULT_TASK), play_with_shal=DEFAULT_POLICY.play_with_shal,
                               play_without_shal=DEFAULT_POLICY.play_without_shal,
                               runs=MIN_RUNS, state_dir=tmp_path)
    assert result["ok"] is True


def test_default_dmm_driver_does_not_leak_past_its_own_context(tmp_path: Path) -> None:
    """CTO review (round 2): whatever the `arena,bench-dmm1` registry slot
    held before a default-policy run -- including nothing at all -- it must
    hold again once `_default_dmm_driver_registered()`'s own `with` block
    exits, so a `--policy` run sharing this process never sees the built-in
    driver it never asked for."""
    from shal import registry

    from shal_arena.bench import DEFAULT_POLICY, DEFAULT_TASK, _default_dmm_driver_registered

    compat = "arena,bench-dmm1"
    before = list(registry._entries.get(compat, []))
    with _default_dmm_driver_registered():
        DEFAULT_POLICY.play_with_shal(str(DEFAULT_TASK), seed=1, state_dir=str(tmp_path))
        # registered for the duration of the call, as the driver itself needs:
        assert len(registry._entries.get(compat, [])) == 1
    after = list(registry._entries.get(compat, []))
    assert after == before
