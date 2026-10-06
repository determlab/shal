"""issue #436: `increment_turns` reads and wrote the turns file with no
lock -- two `shal-arena` calls (each its own process) racing on the same
run could share a nonce and lose a turn. This runs the real thing: N
separate Python PROCESSES, started together, each calling
`increment_turns` exactly once on the SAME run, and checks the final
count and the nonces they each got back."""
from __future__ import annotations

import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from shal_arena.store import RunStore

_N = 8

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

    # every call got its OWN turn count -- none lost to a shared read, none
    # doubled up -- and together they are exactly 1..N.
    assert sorted(nonces) == list(range(1, _N + 1))
    assert store.load(run_id).turns == _N
