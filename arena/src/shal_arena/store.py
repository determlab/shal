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

import json
import secrets
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import UnknownRun

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

    def set_tile(self, run_id: str, address: str, *, case: str, passed: bool) -> RunState:
        state = self.load(run_id)
        if state.status != "open":
            raise UnknownRun(f"run {run_id!r} is closed", fix="start a new run")
        state.tiles[str(address)] = Tile(case=case, passed=passed,
                                         checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime()))
        self._write_public(state)
        return state

    def increment_turns(self, run_id: str) -> RunState:
        """Issue #312 score field ``turns``: one `check` call, whatever its
        result, is one turn. Called before the check itself runs, so a turn
        is counted even if the check later raises."""
        state = self.load(run_id)
        if state.status != "open":
            raise UnknownRun(f"run {run_id!r} is closed", fix="start a new run")
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
