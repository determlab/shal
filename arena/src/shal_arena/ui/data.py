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
        # a capture made on Windows, read back on Linux (or vice versa),
        # leaves the OTHER platform's separator as a literal character --
        # normalize both before taking the stem.
        stem = Path(task_path.replace("\\", "/")).stem
        return load_task(resolve_task(stem))


def _name_for_address(address: str) -> str:
    # "dmm0" -> "DMM", "temp0" -> "TEMP" -- generic enough for "No answer
    # from the DMM" without a per-instrument label table.
    return re.sub(r"\d+$", "", address).upper() or address.upper()


def _answer_label(fault_id: str) -> str:
    # issue #427 CTO review round 2: no "faulty," prefix -- the fault's own
    # name (spaces, not underscores) already says that, e.g. "overheat",
    # "low voltage".
    return "ok" if fault_id == "ok" else fault_id.replace("_", " ")


# issue #427 CTO review round 3: the noise fault gives a DIFFERENT reading
# on every read -- a single "reads X" is misleading when the log has more
# than one distinct value for this address; name the range actually seen
# instead of picking one arbitrarily.
def _varies(values: list[float], decimals: int) -> bool:
    return len({round(v, decimals) for v in values}) > 1


def _rail_clause(rail: dict[str, Any], values: list[float]) -> str:
    label, lo, hi = f"the {rail['name']} rail", rail["lo"], rail["hi"]
    if _varies(values, 2):
        return f"{label} reads {min(values):.2f}-{max(values):.2f} V across reads"
    v = values[-1]
    if lo <= v <= hi:
        return f"{label} reads {v:.2f} V, inside its {lo:.2f}-{hi:.2f} V window"
    side = "below" if v < lo else "above"
    limit = lo if v < lo else hi
    return f"{label} reads {v:.2f} V, {side} its {limit:.2f} V limit"


def _temp_clause(temp: dict[str, Any], values: list[float]) -> str:
    label, high = temp["name"], temp["high_c"]
    if _varies(values, 1):
        return f"{label} reads {min(values):.1f}-{max(values):.1f} °C across reads"
    v = values[-1]
    if v <= high:
        return f"{label} {v:.1f} °C, within its {high:.1f} °C limit"
    return f"{label} {v:.1f} °C, {v - high:.1f} °C above its {high:.1f} °C limit"


def _measured_clause(name: str, rail: dict[str, Any] | None, temp: dict[str, Any] | None,
                     readings: list[float], cause: str | None) -> str | None:
    """`None` means "say nothing about this instrument" -- no attempt was
    ever logged for it (an old capture made before this ticket with no
    `reading` line, or an instrument the agent never touched).

    issue #432 CTO review: the failed-read cause comes from a `failed`
    sim-log line `take_measurement` itself writes (its own except block,
    classifying the real exception -- `shal.errors.HopError`/`HopTimeout`
    is "transport", anything else is the driver's own code), never
    inferred after the fact from whether a `query` line happens to be
    present -- that inference was wrong in practice."""
    if readings:
        if rail is not None:
            return _rail_clause(rail, readings)
        if temp is not None:
            return _temp_clause(temp, readings)
        return f"the {name} reads {readings[-1]:.2f}"
    if cause == "driver":
        return f"the agent's driver failed to read the {name}"
    if cause == "transport":
        return f"No answer from the {name}"
    return None


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
        readings, cause = [], None
        for e in timeline:
            if e.get("address") != instrument["address"]:
                continue
            if e.get("kind") == "reading":
                readings.append(e["detail"]["value"])
            elif e.get("kind") == "failed":
                cause = e["detail"].get("cause")
        clause = _measured_clause(
            _name_for_address(instrument["address"]), rail, temp, readings, cause)
        if clause is not None:
            clauses.append(clause)

    # issue #427 CTO review round 2: "destroyed" must not claim "before any
    # reading" when readings were in fact taken (e.g. a fault was measured,
    # then a later scripted 30 V ask destroyed the card).
    if payload["card"]["destroyed"] and not clauses:
        measured = "nothing -- the card was destroyed before any reading"
    elif clauses:
        measured = ", ".join(clauses)
    else:
        measured = None

    given_label = _answer_label(record["given"])
    # issue #427 CTO review round 3 (blocker): a destroyed card must never
    # say "Correct" -- checked before `record["correct"]`, which only ever
    # compares the agent's answer to the hidden fault and knows nothing
    # about the card's physical state.
    if payload["card"]["destroyed"]:
        verdict = "Wrong: the card is destroyed."
    elif record["correct"]:
        verdict = "Correct."
    elif record["fault_id"] == "ok":
        verdict = "Wrong: the card was fine."
    elif record["given"] == "ok":
        verdict = "Wrong: the card has a fault."
    else:
        verdict = f"Wrong: the real fault was {_answer_label(record['fault_id'])}."

    prefix = f"Measured: {measured}. " if measured is not None else ""
    return f"{prefix}The agent's answer: {given_label}. {verdict}"


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
        "seed": state.seed,
        "status": state.status,
        "closed": closed,
        "turns": state.turns,
        "instruments": instruments,
        "rails": rails,
        "temp_points": temp_points,
        "tiles": tiles,
        "card": {"applied": dict(state.card_applied), "destroyed": state.card_destroyed,
                "power_on": state.card_power_on},
        "timeline": _timeline(run_id, store),
        "record": record,
        "score": score,
    }
    payload["answer_sentence"] = _answer_sentence(payload, rails, temp_points)
    return payload
