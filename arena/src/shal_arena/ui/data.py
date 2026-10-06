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
import re
from pathlib import Path
from typing import Any

from ..errors import TaskFormatError
from ..loader import load_task, resolve_task
from ..replay.card import _timeline  # issue #406: reuse, not a second copy
from ..store import DEFAULT_STATE_DIR, RunStore


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _load_task_for_run(task_path: str) -> Any:
    """issue #427 CTO review: a captured run from another machine carries
    its own absolute `task_path`, which does not exist here. Fall back to
    the packaged task of the same name (the file stem) -- the one thing
    about a capture that travels: a packaged task's own file is always
    named after `task.id` (`arena/src/shal_arena/tasks/<id>.yaml`, same
    rule `loader.list_tasks` reads by). A genuinely unknown task still
    raises `TaskFormatError`, now naming the valid packaged names."""
    try:
        return load_task(task_path)
    except TaskFormatError:
        return load_task(resolve_task(Path(task_path).stem))


def _name_for_address(address: str) -> str:
    # "dmm0" -> "DMM", "temp0" -> "TEMP" -- generic enough for "No answer
    # from the DMM" without a per-instrument label table.
    return re.sub(r"\d+$", "", address).upper() or address.upper()


def _answer_label(fault_id: str) -> str:
    return "ok" if fault_id == "ok" else f"faulty, {fault_id.replace('_', ' ')}"


def _round2(value: float) -> float:
    return round(value, 2)


def _measured_clause(name: str, rail: dict[str, Any] | None, temp: dict[str, Any] | None,
                     reading: float | None) -> str:
    if reading is None:
        return f"No answer from the {name}"
    v = _round2(reading)
    if rail is not None:
        lo, hi = _round2(rail["lo"]), _round2(rail["hi"])
        if lo <= v <= hi:
            return f"the {rail['name']} rail reads {v} V, inside its {lo:.2f}-{hi:.2f} V window"
        side = "below" if v < lo else "above"
        limit = lo if v < lo else hi
        return f"the {rail['name']} rail reads {v} V, {side} its {limit:.2f} V limit"
    if temp is not None:
        high = _round2(temp["high_c"])
        if v <= high:
            return f"{temp['name']} {v} °C, within its {high:.2f} °C limit"
        return f"{temp['name']} {v} °C, {_round2(v - high):.2f} °C above its " \
               f"{high:.2f} °C limit"
    return f"the {name} reads {v}"


def _answer_sentence(payload: dict[str, Any], rails: list[dict[str, Any]],
                     temp_points: list[dict[str, Any]]) -> str | None:
    """issue #427 CTO review: built server-side (not in the page's own JS)
    so a hostile reading value can never reach `innerHTML` at all -- the
    page only ever sets this string via `textContent`. The verdict comes
    from `record["correct"]`, computed by `store.answer`, never
    re-derived here from the agent's own answer text."""
    record = payload["record"]
    if record is None:
        return None
    rails_by_tp = {r["test_point"]: r for r in rails}
    temps_by_tp = {t["test_point"]: t for t in temp_points}
    timeline = payload["timeline"]

    if payload["card"]["destroyed"]:
        measured = "nothing -- the card was destroyed before any reading"
    else:
        clauses = []
        for instrument in payload["instruments"]:
            probe = instrument["probe"]
            if probe is None:
                continue
            test_point = probe.removeprefix("card.")
            rail = rails_by_tp.get(test_point)
            temp = temps_by_tp.get(test_point)
            if rail is None and temp is None:
                continue
            reading = None
            for e in reversed(timeline):
                if e.get("address") == instrument["address"] and e.get("kind") == "reading":
                    reading = e["detail"]["value"]
                    break
            clauses.append(_measured_clause(
                _name_for_address(instrument["address"]), rail, temp, reading))
        if not clauses:
            return None
        measured = ", ".join(clauses)

    given_label = _answer_label(record["given"])
    if record["correct"]:
        verdict = "Correct."
    elif payload["card"]["destroyed"]:
        verdict = "Wrong: the card was destroyed before it could answer."
    elif record["fault_id"] == "ok":
        verdict = "Wrong: the card was fine."
    elif record["given"] == "ok":
        verdict = "Wrong: the card has a fault."
    else:
        verdict = f"Wrong: the real fault was {_answer_label(record['fault_id'])}."

    return f"Measured: {measured}. The agent's answer: {given_label}. {verdict}"


def run_payload(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR) -> dict[str, Any]:
    """Everything the page needs for one run, live or finished. Raises
    `shal_arena.errors.UnknownRun` (same as every other reader) for a run id
    this state dir has never seen."""
    store = RunStore(state_dir)
    state = store.load(run_id)  # UnknownRun if absent -- same error every CLI command uses
    loaded = _load_task_for_run(state.task_path)
    task = loaded.task

    instruments = [
        {"address": str(i.address), "case": i.case, "drives": i.drives, "probe": i.probe}
        for i in task.instruments
    ]
    # issue #406 follow-up: the rail's/temp point's own documented spec --
    # public (the card.yaml, not the hidden fault) -- so the page can state
    # "reads X V, below its Y V limit" without ever touching the fault
    # itself. `test_point` is the key a `task.instruments[].probe` names
    # (e.g. "card.tp_3v3"); `name`/`label` is the human-readable one.
    rails = [
        {"label": r.test_point, "name": r.name.upper(), "test_point": r.test_point,
         "nominal_v": r.nominal_v, "tol_pct": r.tol_pct,
         "lo": r.nominal_v * (1 - r.tol_pct / 100), "hi": r.nominal_v * (1 + r.tol_pct / 100)}
        for r in loaded.card.rails
    ]
    temp_points = [
        {"label": t.test_point, "name": t.name, "test_point": t.test_point,
         "nominal_c": t.nominal_c, "high_c": t.high_c}
        for t in loaded.card.temp_points if t.high_c is not None
    ]
    tiles = {
        addr: {"case": t.case, "passed": t.passed, "checked_at": t.checked_at}
        for addr, t in state.tiles.items()
    }
    closed = state.status == "closed"
    record = _load_json(store.record_path(run_id)) if closed else None
    score = _load_json(store.score_path(run_id)) if closed else None

    payload = {
        "run_id": run_id,
        "task_id": task.id,
        "title": task.title,
        "question": task.question.text,
        "level": task.level,
        "status": state.status,
        "closed": closed,
        "turns": state.turns,
        "instruments": instruments,
        "rails": rails,
        "temp_points": temp_points,
        "tiles": tiles,
        "card": {"applied": dict(state.card_applied), "destroyed": state.card_destroyed},
        "timeline": _timeline(run_id, store),
        "record": record,
        "score": score,
    }
    payload["answer_sentence"] = _answer_sentence(payload, rails, temp_points)
    return payload
