"""Where a run's state lives between separate CLI invocations of ``shal-arena
run`` / ``check`` / ``answer`` (DoD 4: "no file the player can read contains
the hidden fault").

Only one file per run, and it never holds the fault: ``<run_id>.json`` keeps
task/card paths, status, the seed, and the tile each instrument's driver
check has lit so far. The fault itself is never written to disk while a run
is open — `runner.answer` recomputes it from the stored seed (the same
deterministic pick `runner.start_run` made) only at the moment it is needed,
and only the resulting record (written after the player has already
answered) names it (CTO review on #319, c2f3379: an earlier version of this
file persisted the fault to a ``*.secret.json`` next to the public state,
which was on disk, hence readable, for the run's whole open lifetime).
"""
from __future__ import annotations

import contextlib
import json
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import RunClosed, UnknownRun

DEFAULT_STATE_DIR = ".shal-arena"


def new_run_id() -> str:
    return f"run-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}"


@dataclass
class Tile:
    case: str
    passed: bool
    checked_at: str


@dataclass
class RunState:
    run_id: str
    task_path: str
    card_path: str
    status: str                  # "open" | "closed"
    seed: int
    created_at: str
    tiles: dict[str, Tile] = field(default_factory=dict)
    turns: int = 0                # issue #312: a `check` call, win or lose, is a turn
    closed_at: str | None = None
    # issue #313: the card's own CardSim state, carried between the separate
    # CLI invocations of `drive` across one run. Never the hidden fault —
    # just the voltages the player has driven and whether the card is dead.
    card_applied: dict[str, float] = field(default_factory=dict)
    card_destroyed: bool = False
    # issue relay-rail: whether the card's own power relay is energized.
    card_power_on: bool = True
    # issue #325: addresses of DMMs whose current-input fuse has burned.
    fuses_blown: list[str] = field(default_factory=list)


@contextlib.contextmanager
def _locked_turns_file(public_path: Path):
    """issue #436: an OS-level exclusive lock, held for one run's own
    read-modify-write of `turns`, so two `shal-arena` processes racing on
    the same run serialize instead of overwriting each other. A sidecar
    ``<run>.turns.lock`` file (never the public state file itself, which
    `_write_public` replaces wholesale each call) -- `flock` on POSIX,
    `msvcrt.locking` on Windows; both block until the other process's lock
    releases, so this never busy-waits past the other call's own short
    critical section."""
    public_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = public_path.with_suffix(public_path.suffix + ".turns.lock")
    with lock_path.open("a+b") as f:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.01)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


class RunStore:
    """File-backed run state under ``state_dir`` (default ``./.shal-arena``,
    created on first use)."""

    def __init__(self, state_dir: str | Path = DEFAULT_STATE_DIR) -> None:
        self.dir = Path(state_dir)

    def _public_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def _record_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.record.json"

    def record_path(self, run_id: str) -> Path:
        return self._record_path(run_id)

    def sim_log_path(self, run_id: str) -> Path:
        """Issue #312: the sim log is player-readable (it never names the
        fault, only SCPI commands and times), so it lives right next to the
        public run json, not under a separate private path."""
        return self.dir / f"{run_id}.simlog.jsonl"

    def score_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.score.json"

    def write_score(self, run_id: str, score: dict) -> Path:
        path = self.score_path(run_id)
        path.write_text(json.dumps(score, indent=2), encoding="utf-8")
        return path

    def create(self, *, task_path: str, card_path: str, seed: int) -> RunState:
        self.dir.mkdir(parents=True, exist_ok=True)
        run_id = new_run_id()
        state = RunState(run_id=run_id, task_path=task_path, card_path=card_path,
                         status="open", seed=seed,
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._write_public(state)
        return state

    def _write_public(self, state: RunState) -> None:
        doc = asdict(state)
        self._public_path(state.run_id).write_text(json.dumps(doc, indent=2), encoding="utf-8")

    def load(self, run_id: str) -> RunState:
        path = self._public_path(run_id)
        if not path.is_file():
            raise UnknownRun(
                f"no run {run_id!r} in {self.dir}",
                fix="run `shal-arena run <task.yaml> --json` to start one, and use the "
                    "run_id it returns")
        doc = json.loads(path.read_text(encoding="utf-8"))
        tiles = {addr: Tile(**t) for addr, t in doc.pop("tiles", {}).items()}
        return RunState(tiles=tiles, **doc)

    def load_open(self, run_id: str) -> RunState:
        """`load`, but refuses a closed run (issue #325), naming the fix."""
        state = self.load(run_id)
        if state.status != "open":
            raise RunClosed(
                f"run {run_id} is closed; it takes no more input",
                fix="start a new run with `shal-arena run <task.yaml> --json`")
        return state

    def set_fuse_blown(self, run_id: str, address: str) -> RunState:
        state = self.load_open(run_id)
        if str(address) not in state.fuses_blown:
            state.fuses_blown.append(str(address))
            self._write_public(state)
        return state

    def set_tile(self, run_id: str, address: str, *, case: str, passed: bool) -> RunState:
        state = self.load_open(run_id)
        state.tiles[str(address)] = Tile(case=case, passed=passed,
                                         checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime()))
        self._write_public(state)
        return state

    def set_card_state(self, run_id: str, *, applied: dict[str, float],
                      destroyed: bool) -> RunState:
        """issue #313: persist `CardSim.applied`/`CardSim.destroyed` after a
        `drive` call, so the next CLI invocation for this run restores the
        same card instead of starting it fresh from nominal every time."""
        state = self.load_open(run_id)
        state.card_applied = dict(applied)
        state.card_destroyed = destroyed
        self._write_public(state)
        return state

    def set_card_power(self, run_id: str, power_on: bool) -> RunState:
        """issue relay-rail: persist the card's own relay state between
        separate CLI invocations of `call`, the same way `set_card_state`
        persists `CardSim.applied`/`destroyed` after a `drive` call."""
        state = self.load_open(run_id)
        state.card_power_on = bool(power_on)
        self._write_public(state)
        return state

    def increment_turns(self, run_id: str) -> RunState:
        """Issue #312 score field ``turns``: one call that reaches the sim
        (`check`, `measure`, `drive`), whatever its result, is one turn.
        Called before the call itself runs, so a turn is counted even if it
        later raises. A closed run refuses (issue #325).

        Issue #436: `shal-arena` is a fresh process per call, so two
        measures started at the same moment can both read the same
        pre-increment `turns`, then both write the same post-increment
        value back -- one turn lost, and (issue #433) the `noise` fault's
        own per-call nonce is this same counter, so two reads can also
        share a ripple sample. `_locked_turns_file` serializes the whole
        read-modify-write under an OS file lock, so this is atomic across
        processes, not just within one."""
        with _locked_turns_file(self._public_path(run_id)):
            state = self.load_open(run_id)
            state.turns += 1
            self._write_public(state)
        return state

    def answer(self, run_id: str, *, given: str, fault_id: str) -> dict[str, Any]:
        """Close the run and write/return the record. ``fault_id`` is the
        caller's job to recompute (from the public ``state.seed``) — this
        store never holds it before this call."""
        state = self.load(run_id)
        if state.status != "open":
            raise UnknownRun(f"run {run_id!r} is already closed", fix="start a new run")
        record = {
            "run_id": run_id,
            "task_path": state.task_path,
            "card_path": state.card_path,
            "given": given,
            "fault_id": fault_id,
            "correct": given == fault_id,
            "closed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self._record_path(run_id).write_text(json.dumps(record, indent=2), encoding="utf-8")
        state.status = "closed"
        state.closed_at = record["closed_at"]
        self._write_public(state)
        return record
