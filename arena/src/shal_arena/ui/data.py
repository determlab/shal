"""The one payload both the live server and the export build from (issue
#406). Reads only this run's own files: the public state (`<id>.json`), the
sim log (`<id>.simlog.jsonl`), and -- only once the run is closed -- the
record and score files. That last rule is structural, the same way
`replay/card.py` already holds it: `record.json`/`score.json` are read only
when `state.status == "closed"`, and nothing before that point ever touches
them, so there is no path through this module that could leak the fault
before the run ends.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..loader import load_task
from ..replay.card import _timeline  # issue #406: reuse, not a second copy
from ..store import DEFAULT_STATE_DIR, RunStore


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def run_payload(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Everything the page needs for one run, live or finished. Raises
    `shal_arena.errors.UnknownRun` (same as every other reader) for a run id
    this state dir has never seen."""
    store = RunStore(state_dir)
    state = store.load(run_id)  # UnknownRun if absent -- same error every CLI command uses
    loaded = load_task(state.task_path)
    task = loaded.task

    instruments = [
        {"address": str(i.address), "case": i.case, "drives": i.drives, "probe": i.probe}
        for i in task.instruments
    ]
    tiles = {
        addr: {"case": t.case, "passed": t.passed, "checked_at": t.checked_at}
        for addr, t in state.tiles.items()
    }
    closed = state.status == "closed"
    record = _load_json(store.record_path(run_id)) if closed else None
    score = _load_json(store.score_path(run_id)) if closed else None

    return {
        "run_id": run_id,
        "task_id": task.id,
        "title": task.title,
        "question": task.question.text,
        "status": state.status,
        "closed": closed,
        "turns": state.turns,
        "instruments": instruments,
        "tiles": tiles,
        "card": {"applied": dict(state.card_applied), "destroyed": state.card_destroyed},
        "timeline": _timeline(run_id, store),
        "record": record,
        "score": score,
    }
