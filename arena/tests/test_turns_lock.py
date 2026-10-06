"""issue #436: `increment_turns` reads and wrote the turns file with no
lock -- two `shal-arena` calls (each its own process) racing on the same
run could share a nonce and lose a turn.

Two tests, two jobs:

- `test_n_concurrent_threads_each_get_one_distinct_turn` is the
  DETERMINISTIC guard (CTO review on #436's first PR: the original
  subprocess-timing version only caught a missing lock 2 times out of 8).
  It patches `RunStore._write_public` to sleep INSIDE the critical
  section, between the read and the write -- widening the race window so
  wide that a build with the lock removed loses a turn on every single
  run, not just sometimes, while a build WITH the lock (this one) stays
  correct regardless of the delay, since the lock serializes the whole
  section regardless of how long it takes.
- `test_n_parallel_processes_each_get_one_distinct_turn` is the real
  end-to-end proof: N separate Python PROCESSES (not threads in one
  process -- the actual shape issue #436 reports, a fresh `shal-arena`
  call each time), started together, each calling `increment_turns`
  exactly once on the SAME run.
"""
from __future__ import annotations

import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from shal_arena.store import RunStore

_N = 8


def test_n_concurrent_threads_each_get_one_distinct_turn(tmp_path: Path, monkeypatch) -> None:
    store = RunStore(tmp_path)
    state = store.create(task_path="t.yaml", card_path="c.yaml", seed=0)
    run_id = state.run_id

    real_write_public = RunStore._write_public

    def slow_write_public(self, state):
        # widen the window between "read the old turns" and "write the
        # new one" so an unlocked build overlaps on EVERY run, not just
        # when the OS happens to schedule two processes close together.
        time.sleep(0.05)
        real_write_public(self, state)

    monkeypatch.setattr(RunStore, "_write_public", slow_write_public)

    with ThreadPoolExecutor(max_workers=_N) as pool:
        nonces = list(pool.map(lambda _: store.increment_turns(run_id).turns, range(_N)))

    assert sorted(nonces) == list(range(1, _N + 1))
    assert store.load(run_id).turns == _N


_SCRIPT = (
    "from shal_arena.store import RunStore\n"
    "store = RunStore({state_dir!r})\n"
    "state = store.increment_turns({run_id!r})\n"
    "print(state.turns)\n"
)


def _call_in_a_fresh_process(state_dir: Path, run_id: str) -> int:
    proc = subprocess.run(
        [sys.executable, "-c", _SCRIPT.format(state_dir=str(state_dir), run_id=run_id)],
        capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return int(proc.stdout.strip())


def test_n_parallel_processes_each_get_one_distinct_turn(tmp_path: Path) -> None:
    store = RunStore(tmp_path)
    state = store.create(task_path="t.yaml", card_path="c.yaml", seed=0)
    run_id = state.run_id

    with ThreadPoolExecutor(max_workers=_N) as pool:
        nonces = list(pool.map(lambda _: _call_in_a_fresh_process(tmp_path, run_id), range(_N)))

    assert sorted(nonces) == list(range(1, _N + 1))
    assert store.load(run_id).turns == _N


def test_lock_timeout_names_the_lock_file_and_the_fix(tmp_path: Path, monkeypatch) -> None:
    """issue #436 CTO review: the lock must not block forever -- a process
    that holds it past a deadline is reported, not hung on silently."""
    from shal_arena import store as store_mod
    from shal_arena.errors import LockTimeout

    monkeypatch.setattr(store_mod, "_LOCK_TIMEOUT_S", 0.3)
    store = RunStore(tmp_path)
    state = store.create(task_path="t.yaml", card_path="c.yaml", seed=0)
    run_id = state.run_id
    public_path = tmp_path / f"{run_id}.json"
    lock_path = public_path.with_suffix(".json.lock")
    sentinel = tmp_path / "holder-ready"

    # hold the lock from a real second process for longer than the
    # timeout -- a sentinel file (written right after the lock is
    # acquired) replaces a guessed sleep, so this never races against how
    # long the holder's own interpreter startup happens to take.
    holder_script = (
        "import time\n"
        "from pathlib import Path\n"
        "from shal_arena.store import _locked_state_file\n"
        "with _locked_state_file(Path({public_path!r}), timeout=5):\n"
        "    Path({sentinel!r}).write_text('ready')\n"
        "    time.sleep(2.0)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c",
         holder_script.format(public_path=str(public_path), sentinel=str(sentinel))])
    try:
        deadline = time.monotonic() + 10
        while not sentinel.is_file():
            assert time.monotonic() < deadline, "holder process never acquired the lock"
            time.sleep(0.01)
        with pytest.raises(LockTimeout) as ei:
            store.increment_turns(run_id)
        assert str(lock_path) in str(ei.value)
        assert ei.value.fix
    finally:
        proc.wait(timeout=10)
