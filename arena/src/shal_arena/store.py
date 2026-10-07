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
import os
import secrets
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import FileBusy, LockTimeout, RunClosed, UnknownRun

#: issue #436 CTO review: a lock that never gives up can hang a whole run on
#: one stuck process (crashed mid-write, a debugger attached to it) --
#: generous for a critical section this short (one JSON read + write), but
#: finite.
_LOCK_TIMEOUT_S = 10.0

#: issue #436 CTO review round 3: a READER outside the lock (`shal-arena ui`
#: polling every 1s, `replay`, `bench`) can open the public state file in
#: the instant between `os.replace` swapping it in and the OS actually
#: settling the rename -- Windows briefly denies a second open of a file
#: mid-rename (`PermissionError [WinError 5]`), where POSIX's rename is a
#: single atomic syscall with no such window. A short retry absorbs it.
_IO_RETRY_TIMEOUT_S = 2.0

DEFAULT_STATE_DIR = ".shal-arena"


def _retry_on_permission_error(fn, *, path: Path, timeout: float = _IO_RETRY_TIMEOUT_S):
    """issue #449: past the deadline, a raw `PermissionError` would reach the
    CLI as a traceback with no fix. Wrap it in `FileBusy`, naming `path` and
    what to do, keeping the original as `__cause__` (`raise ... from err`)."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            return fn()
        except PermissionError as err:
            if time.monotonic() >= deadline:
                raise FileBusy(
                    f"{path}: another program has the run file open; close it and retry",
                    fix="close whatever program has the run file open, then run the "
                        "command again") from err
            time.sleep(0.005)


def _ensure_lock_byte(lock_path: Path) -> None:
    """issue #436 CTO review round 3: the lock file needs one real byte for
    Windows's byte-RANGE `msvcrt.locking` to lock at all (POSIX's `flock`
    locks the whole file regardless, so this is a no-op need on POSIX, but
    harmless to always do). Called once, from `RunStore.create`, before
    any other process could possibly be racing on this run's own lock
    file -- never lazily from inside the lock's own acquisition, which is
    exactly where two first-openers used to race each other."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        if lock_path.stat().st_size > 0:
            return
    except FileNotFoundError:
        pass
    try:
        with lock_path.open("ab") as f:
            if f.tell() == 0:
                f.write(b"0")
    except PermissionError:
        pass  # another process is mid-write to the same byte -- fine, it exists either way


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
    # issue #478: the fault list this run's fault was drawn from, in draw
    # order, as positions in its card's `faults:` list (never the ids: no
    # file names a fault while the run is open -- the same list for every
    # seed, so it says nothing about which one was drawn), and the game
    # rules it was started under. A run stored before these existed has
    # neither (`None`): its fault was drawn from `fault.legacy_fault_ids`.
    fault_indices: list[int] | None = None
    game_version: str | None = None


@contextlib.contextmanager
def _locked_state_file(public_path: Path, *, timeout: float | None = None):
    """issue #436: an OS-level exclusive lock, held for one run's own
    read-modify-write of its public state file, so two `shal-arena`
    processes racing on the same run serialize instead of overwriting each
    other (or, #436 CTO review, both reading the same pre-write state and
    both writing the same post-write one back). A sidecar
    ``<run>.json.lock`` file (never the public state file itself, which
    `_write_public` replaces atomically each call) -- `flock` on POSIX,
    `msvcrt.locking` on Windows.

    Neither primitive blocks forever by default here: both are polled on a
    short interval against a wall-clock deadline, so a process that died
    mid-write (or a debugger attached to it) raises `LockTimeout` --
    naming the lock file and the fix -- instead of hanging every other
    call on this run indefinitely."""
    if timeout is None:
        timeout = _LOCK_TIMEOUT_S  # read at call time, not def time -- a test can patch it
    public_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = public_path.with_suffix(public_path.suffix + ".lock")
    _ensure_lock_byte(lock_path)
    deadline = time.monotonic() + timeout
    with lock_path.open("a+b") as f:
        if sys.platform == "win32":
            import msvcrt
            # `LK_LOCK`/`LK_NBLCK` lock a byte RANGE at the current
            # position -- `_ensure_lock_byte` (called once, from
            # `RunStore.create`, before any concurrent access of this run
            # is possible) already guarantees that byte exists; every
            # opener here just seeks to it. (issue #436 CTO review round
            # 3: writing that byte lazily, HERE, raced two first-openers
            # against each other -- Windows denies a write into a byte
            # range the other side has already locked, so the loser's
            # `flush()` raised `PermissionError`.)
            f.seek(0)
            while True:
                try:
                    # `LK_LOCK` has its OWN built-in blocking retry (up to
                    # ~10 one-second attempts) before it raises -- it would
                    # swallow our deadline check inside one call. `LK_NBLCK`
                    # fails immediately instead, so this loop's own
                    # `deadline` is what actually governs how long this
                    # waits.
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"could not lock {lock_path} within {timeout:.0f}s "
                            "-- another shal-arena process may be stuck",
                            fix=f"check for a stuck shal-arena process, then "
                                f"delete {lock_path} if none is running") from None
                    time.sleep(0.01)
            try:
                yield
            finally:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            while True:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise LockTimeout(
                            f"could not lock {lock_path} within {timeout:.0f}s "
                            "-- another shal-arena process may be stuck",
                            fix=f"check for a stuck shal-arena process, then "
                                f"delete {lock_path} if none is running") from None
                    time.sleep(0.01)
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
        # issue #435: the old bare name read as a SHAL core record (the
        # core package's own record format) to anyone who hasn't read this
        # file's own code -- this is the arena's own run record, a
        # different shape entirely. A run `answer`s from here on always
        # WRITES the new name -- `record_path` below is the one place that
        # also reads the old one, for a capture made before this rename.
        return self.dir / f"{run_id}.arena-record.json"

    def _legacy_record_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.record.json"

    def record_path(self, run_id: str) -> Path:
        """The run's own record file -- the new name, or (issue #435 CTO
        review: "export and verify must still read old captures") the old
        bare `record.json` name, for a capture made before this rename and
        never migrated. Never writes either path; only picks which one a
        reader should open."""
        new_path = self._record_path(run_id)
        if not new_path.is_file() and self._legacy_record_path(run_id).is_file():
            return self._legacy_record_path(run_id)
        return new_path

    def sim_log_path(self, run_id: str) -> Path:
        """Issue #312: the sim log is player-readable (it never names the
        fault, only SCPI commands and times), so it lives right next to the
        public run json, not under a separate private path."""
        return self.dir / f"{run_id}.simlog.jsonl"

    def cli_log_path(self, run_id: str) -> Path:
        """Issue #460: one JSON line per `shal-arena` CLI call this run
        served, next to the public run json, same as every other run file."""
        return self.dir / f"{run_id}.cli.jsonl"

    def append_cli_log(self, run_id: str, entry: dict) -> None:
        """Appends one line (issue #460). Under the same per-run lock
        #436/#442 already added for the state file — agents run commands in
        parallel, and this is append-only, but a lock avoids relying on
        O_APPEND's atomicity guarantees differing by platform.

        Writes only for a run id with no path separator whose public run
        file already exists (round 2 must-fix): `cli.py` passes the run id
        straight from argv or a reply, unchecked, and this is the one place
        it ever reaches a filesystem path. Without this check, `answer
        no-such-run ok` left an orphan `no-such-run.cli.jsonl`, and
        `answer ../escaped ok` wrote `<state_dir>/../escaped.cli.jsonl` —
        outside the state dir entirely. A run id that fails either check
        has no run to log against, so the entry is silently skipped rather
        than raised: it is observability, never part of the command's own
        result."""
        if "/" in run_id or "\\" in run_id:
            return
        if not self._public_path(run_id).is_file():
            return
        path = self.cli_log_path(run_id)
        with _locked_state_file(self._public_path(run_id)):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")

    def score_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.score.json"

    def write_score(self, run_id: str, score: dict) -> Path:
        path = self.score_path(run_id)
        path.write_text(json.dumps(score, indent=2), encoding="utf-8")
        return path

    def create(self, *, task_path: str, card_path: str, seed: int,
               fault_indices: list[int] | None = None,
               game_version: str | None = None) -> RunState:
        self.dir.mkdir(parents=True, exist_ok=True)
        run_id = new_run_id()
        state = RunState(run_id=run_id, task_path=task_path, card_path=card_path,
                         status="open", seed=seed,
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                         fault_indices=None if fault_indices is None else list(fault_indices),
                         game_version=game_version)
        self._write_public(state)
        # issue #436 CTO review round 3: create the lock file's one byte
        # HERE, before any other process could possibly be racing on this
        # fresh run_id's own lock -- not lazily inside the lock's own
        # acquisition, which is exactly where two first-openers raced.
        public_path = self._public_path(run_id)
        _ensure_lock_byte(public_path.with_suffix(public_path.suffix + ".lock"))
        return state

    def _write_public(self, state: RunState) -> None:
        # issue #436 CTO review: write to a sibling temp file, then
        # `os.replace` it over the real path -- `os.replace` is an atomic
        # rename on both POSIX and Windows (unlike a direct
        # `path.write_text`, which a reader could observe mid-write, or
        # which a crash between open and close could leave truncated).
        #
        # issue #436 CTO review round 3: a READER outside the lock
        # (`shal-arena ui` polling every 1s, `replay`, `bench`) can have
        # the destination open at the exact instant `os.replace` runs --
        # Windows denies the rename itself while another handle has the
        # target open (`PermissionError [WinError 5]`); POSIX's rename
        # has no such window. Retry briefly; clean up the temp file
        # either way so a run that outlives the retry deadline doesn't
        # also leak one.
        path = self._public_path(state.run_id)
        doc = asdict(state)
        tmp_path = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
        tmp_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        try:
            _retry_on_permission_error(lambda: os.replace(tmp_path, path), path=path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def load(self, run_id: str) -> RunState:
        path = self._public_path(run_id)
        if not path.is_file():
            raise UnknownRun(
                f"no run {run_id!r} in {self.dir}",
                fix="run `shal-arena run <task.yaml> --json` to start one, and use the "
                    "run_id it returns")
        # issue #436 CTO review round 3: the same reader-vs-os.replace
        # window applies to a plain read -- retry it too.
        text = _retry_on_permission_error(lambda: path.read_text(encoding="utf-8"), path=path)
        doc = json.loads(text)
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
        with _locked_state_file(self._public_path(run_id)):
            state = self.load_open(run_id)
            if str(address) not in state.fuses_blown:
                state.fuses_blown.append(str(address))
                self._write_public(state)
        return state

    def set_tile(self, run_id: str, address: str, *, case: str, passed: bool) -> RunState:
        with _locked_state_file(self._public_path(run_id)):
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
        with _locked_state_file(self._public_path(run_id)):
            state = self.load_open(run_id)
            state.card_applied = dict(applied)
            state.card_destroyed = destroyed
            self._write_public(state)
        return state

    def set_card_power(self, run_id: str, power_on: bool) -> RunState:
        """issue relay-rail: persist the card's own relay state between
        separate CLI invocations of `call`, the same way `set_card_state`
        persists `CardSim.applied`/`destroyed` after a `drive` call."""
        with _locked_state_file(self._public_path(run_id)):
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
        share a ripple sample. `_locked_state_file` serializes the whole
        read-modify-write under an OS file lock, so this is atomic across
        processes, not just within one. CTO review: every caller must use
        THIS call's own returned state afterward, never a separate
        `store.load` -- that would re-read outside the lock."""
        with _locked_state_file(self._public_path(run_id)):
            state = self.load_open(run_id)
            state.turns += 1
            self._write_public(state)
        return state

    def answer(self, run_id: str, *, given: str, fault_id: str,
               expected: str | None = None) -> dict[str, Any]:
        """Close the run and write/return the record. ``fault_id`` is the
        caller's job to recompute (from the public ``state.seed``) — this
        store never holds it before this call. ``expected`` (issue #478) is
        the answer that names it, when that is not the id itself
        (``broken_probe`` is answered ``probe``)."""
        if expected is None:
            expected = fault_id
        with _locked_state_file(self._public_path(run_id)):
            state = self.load(run_id)
            if state.status != "open":
                raise UnknownRun(f"run {run_id!r} is already closed", fix="start a new run")
            record = {
                "run_id": run_id,
                "task_path": state.task_path,
                "card_path": state.card_path,
                "given": given,
                "fault_id": fault_id,
                "correct": given == expected,
                "closed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            self._record_path(run_id).write_text(json.dumps(record, indent=2),
                                                  encoding="utf-8")
            state.status = "closed"
            state.closed_at = record["closed_at"]
            self._write_public(state)
        return record
