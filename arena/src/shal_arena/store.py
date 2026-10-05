"""Where a run's state lives between separate CLI invocations of ``shal-arena
run`` / ``check`` / ``answer`` (DoD 4: "no file the player can read contains
the hidden fault").

Two files per run, deliberately split:

- ``<run_id>.json`` — public: task/card paths, status, and the tile each
  instrument's driver check has lit so far. This is the only state file a
  player-facing command ever prints from.
- ``<run_id>.secret.json`` — the fault id picked by the seed. Never returned
  by any command, never merged into the public file, and deleted the moment
  `close` reveals it into the final record — so after a run ends there is no
  file left that still calls itself "secret" but isn't.
"""
from __future__ import annotations

import json
import os
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
    closed_at: str | None = None


class RunStore:
    """File-backed run state under ``state_dir`` (default ``./.shal-arena``,
    created on first use)."""

    def __init__(self, state_dir: str | Path = DEFAULT_STATE_DIR) -> None:
        self.dir = Path(state_dir)

    def _public_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.json"

    def _secret_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.secret.json"

    def _record_path(self, run_id: str) -> Path:
        return self.dir / f"{run_id}.record.json"

    def create(self, *, task_path: str, card_path: str, seed: int, fault_id: str) -> RunState:
        self.dir.mkdir(parents=True, exist_ok=True)
        run_id = new_run_id()
        state = RunState(run_id=run_id, task_path=task_path, card_path=card_path,
                         status="open", seed=seed,
                         created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._write_public(state)
        self._secret_path(run_id).write_text(json.dumps({"fault_id": fault_id}), encoding="utf-8")
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

    def _load_secret(self, run_id: str) -> dict[str, Any]:
        path = self._secret_path(run_id)
        if not path.is_file():
            # closed runs delete their secret file (it has already been revealed
            # into the record) — reaching here on an "open" run would be a bug
            raise UnknownRun(f"run {run_id!r} has no secret state left (already closed)",
                             fix="start a new run with `shal-arena run`")
        return json.loads(path.read_text(encoding="utf-8"))

    def set_tile(self, run_id: str, address: str, *, case: str, passed: bool) -> RunState:
        state = self.load(run_id)
        if state.status != "open":
            raise UnknownRun(f"run {run_id!r} is closed", fix="start a new run")
        state.tiles[str(address)] = Tile(case=case, passed=passed,
                                         checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                                                   time.gmtime()))
        self._write_public(state)
        return state

    def answer(self, run_id: str, *, given: str) -> dict[str, Any]:
        """Close the run, reveal the fault, and write/return the record."""
        state = self.load(run_id)
        if state.status != "open":
            raise UnknownRun(f"run {run_id!r} is already closed", fix="start a new run")
        fault_id = self._load_secret(run_id)["fault_id"]
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
        os.remove(self._secret_path(run_id))
        return record
