#!/usr/bin/env python
"""Evidence page (shal#380): one static HTML page built from the evidence.json
files of a clean-machine run (dev/e2e/story.py, workflow
.github/workflows/e2e-clean-machine.yml).

Each matrix cell (one OS x one Python) uploads its own `evidence-<os>-<py>`
artifact holding a single evidence.json: `os`, `python`, `run_id`,
`run_attempt`, `versions` (one entry per package: version, sha, repo,
filename, sha256) and `checks[]` (`id`, `result`, `log`). After `gh run
download <run-id> -D ev`, `ev/` holds one subdirectory per artifact. This
script reads every subdirectory of the directory it is given, writes one
HTML page listing every cell's versions and checks, and prints a count of
how many cells passed, failed, or were missing.

A cell is `missing` when its evidence.json is absent or cannot be parsed as
the shape above -- never silently skipped, so a reviewer (or an agent reading
`--json`) sees it just like a failed one.

CTO review on #380: a macOS cell never gates the merge decision (same ruling
as the workflow's own `continue-on-error: macos-latest`), so every count that
decides the exit code excludes it; a macOS cell still renders, still shown
FAIL/PASS, just labelled non-gating. A cell whose `run_attempt` is more than
1 is flagged: the gate rule is "green on attempt 1", so a pass on retry does
not count towards it.

shal#402 (D6 spec; CTO review on #380, module docstring lines 19-23): the
gate is 3 DAILY runs in a row, never one run. This page prints the one-run
verdict as `this run: N/M gating cells pass (day D of 3)`; `gate met` is
printed only when this run and the 2 immediately preceding daily runs (read
from `--history FILE`, a JSON list of past runs oldest-first) were each a
scheduled run, attempt 1, every gating cell green, on 3 distinct, exactly
consecutive calendar days ending today. With no `--history`, day is always
1 (nothing to look back on) and the gate is never met from a single run.

Round 2 (CTO review on PR #467): TODAY's own run is held to the same 3
conditions as a history entry, not just its cell counts -- `--event` names
today's own trigger (the workflow passes `${{ github.event_name }}`) and
every gating cell's own `run_attempt` must be 1; without `--event`, today
can never be more than day 1 (clean, but it cannot anchor a streak). Without
`--date` (today's own UTC date, `YYYY-MM-DD` -- never `datetime.now()`,
so this is deterministic and testable), the streak can't be verified against
history either, for the same reason: day stays at most 1.

Usage:
    evidence_page.py DIR --out evidence.html [--history runs.json]
                      [--event schedule] [--date 2026-01-02]
                      [--expect-gating 4] [--json]

Exit 0 if every gating cell passed, 1 if a gating check failed or a gating
cell is missing (a macOS-only failure never sets this) -- unchanged by the
3-day gate above, which decides only the page's verdict text.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_MISSING = "missing"

#: CTO review on #380 (the D6 spec): what "gate met" means, printed verbatim
#: on the page so nobody has to go find the ruling.
GATE_RULE = "3 daily runs in a row, green on attempt 1, all non-macOS cells"


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-")


def _is_macos(name: str, os_label: str | None) -> bool:
    return "macos" in (os_label or name).lower()


def _empty_cell(name: str) -> dict[str, Any]:
    return {"name": name, "status": STATUS_MISSING, "os": None, "python": None,
            "run_id": None, "run_attempt": None, "versions": {}, "checks": [],
            "gating": not _is_macos(name, None)}


def _load_cell(cell_dir: Path) -> dict[str, Any]:
    """Read one cell's evidence.json. Never raises -- an absent or
    unparseable file (or one missing the shape #380 requires) comes back as
    a `missing` cell instead, per the Constraints: never skipped."""
    name = cell_dir.name
    try:
        doc = json.loads((cell_dir / "evidence.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_cell(name)

    if not isinstance(doc, dict) or not isinstance(doc.get("checks"), list):
        return _empty_cell(name)

    checks = doc["checks"]
    # CTO review on #467 round 3, must-fix 2a: `any([])` is False, so a
    # cell with an empty checks list used to read as a pass -- nothing was
    # actually proved. Treat it the same as an unreadable evidence.json:
    # missing, not passed.
    if not checks:
        status = STATUS_MISSING
    else:
        status = STATUS_FAIL if any(c.get("result") != "pass" for c in checks) else STATUS_PASS
    os_label = doc.get("os")
    return {"name": name, "status": status, "os": os_label, "python": doc.get("python"),
            "run_id": doc.get("run_id"), "run_attempt": doc.get("run_attempt"),
            "versions": doc.get("versions") or {}, "checks": checks,
            "gating": not _is_macos(name, os_label)}


def load_cells(root: Path) -> list[dict[str, Any]]:
    return [_load_cell(p) for p in sorted(root.iterdir()) if p.is_dir()]


def _counts(cells: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "cells": len(cells),
        "passed": sum(1 for c in cells if c["status"] == STATUS_PASS),
        "failed": sum(1 for c in cells if c["status"] == STATUS_FAIL),
        "missing": sum(1 for c in cells if c["status"] == STATUS_MISSING),
    }


def summarize(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Overall counts across every cell, plus a `gating` breakdown that
    leaves macOS out -- that second one is what the exit code is based on
    (CTO review on #380). The page's "gate met" verdict is a separate,
    3-daily-run decision (`gate_status`, shal#402); this run's own gating
    counts only feed the one-run verdict line."""
    overall = _counts(cells)
    overall["gating"] = _counts([c for c in cells if c["gating"]])
    return overall


def _is_retry(run_attempt: Any) -> bool:
    try:
        return int(run_attempt) > 1
    except (TypeError, ValueError):
        return False


#: shal#402/#467: the shape --history takes, named in every error about it.
HISTORY_FORMAT = (
    'a JSON list of prior runs, oldest first, each {"date": <"YYYY-MM-DD">, '
    '"run_id": <non-empty str>, "event": <str>, "attempt": <int, not bool>, '
    '"gating_passed": <bool>}')


def _history_error(path: Path, message: str, *, index: int | None = None) -> ValueError:
    """CTO review on #467, must-fix 4 (and the round-3 nit: the same next-
    step line for a whole-file error, not only a bad entry): name which
    entry is bad, or that the whole file itself is unreadable, and the next
    step -- fix it, or drop --history entirely (this run then shows day 1
    of 3 on its own, never an error)."""
    where = f"entry {index}: " if index is not None else ""
    return ValueError(
        f"--history {path}: {where}{message}; expected {HISTORY_FORMAT}. "
        f"Fix it, or drop --history (this run then shows day 1 of 3).")


def load_history(path: Path) -> list[dict[str, Any]]:
    """`--history FILE`: the runs immediately before this one, oldest
    first, ending with the most recent run before this one. Never a
    dashboard of every run ever -- only enough to look back 2 days (the gate
    needs 3 in a row including today). Raises ValueError, naming
    `HISTORY_FORMAT` and the bad entry's own index, for anything that
    cannot be read as that shape (shal#402 Agent path: "an error for a bad
    history file names the expected format").

    CTO review on #467, must-fix 3: types are checked, not just presence --
    `"gating_passed": "false"` is a real string, not the boolean `False`,
    and must not be read as truthy; `"attempt": true`/`1.0` must not pass as
    the int `1` (`bool` is a subclass of `int` in Python, so `isinstance`
    alone is not enough -- excluded explicitly)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise _history_error(path, f"cannot read file ({e})") from e
    except json.JSONDecodeError as e:
        raise _history_error(path, f"not valid JSON ({e})") from e
    if not isinstance(doc, list):
        raise _history_error(path, f"expected a list, got {type(doc).__name__}")
    required = {"date", "run_id", "event", "attempt", "gating_passed"}
    for i, entry in enumerate(doc):
        if not (isinstance(entry, dict) and required <= entry.keys()):
            raise _history_error(path, "missing one of date/run_id/event/attempt/"
                                       "gating_passed", index=i)
        if not isinstance(entry["date"], str) or not _is_iso_date(entry["date"]):
            raise _history_error(path, f'"date" must be "YYYY-MM-DD", got '
                                       f'{entry["date"]!r}', index=i)
        # round 4 nit: "unknown" (story.py's own value outside CI) is
        # rejected the same way here as in `today_entry` -- the same rule
        # in both places, not just one.
        if (not isinstance(entry["run_id"], str) or not entry["run_id"]
                or entry["run_id"] == "unknown"):
            raise _history_error(path, f'"run_id" must be a non-empty string, not "unknown", '
                                       f'got {entry["run_id"]!r}', index=i)
        if not isinstance(entry["event"], str):
            raise _history_error(path, f'"event" must be a string, got '
                                       f'{entry["event"]!r}', index=i)
        if isinstance(entry["attempt"], bool) or not isinstance(entry["attempt"], int):
            raise _history_error(path, f'"attempt" must be an int, got '
                                       f'{entry["attempt"]!r}', index=i)
        if not isinstance(entry["gating_passed"], bool):
            raise _history_error(path, f'"gating_passed" must be true or false, got '
                                       f'{entry["gating_passed"]!r}', index=i)
    return doc


def _is_iso_date(text: str) -> bool:
    try:
        date_cls.fromisoformat(text)
        return True
    except ValueError:
        return False


def _qualifies(entry: dict[str, Any]) -> bool:
    """One entry (today's own, or a history one) counts towards the streak
    only if it was a scheduled run (not a manual/PR run), green on the
    first attempt, and every gating cell passed -- the exact 3 conditions
    #402's gate rule names. `is True`, not `bool(...)` (CTO review on #467,
    must-fix 3): a truthy non-bool must never have reached this point --
    `load_history` already rejected it for a history entry, and today's own
    value is built by this module, never parsed from untrusted JSON."""
    return (entry.get("event") == "schedule" and entry.get("attempt") == 1
            and entry.get("gating_passed") is True)


def gate_status(today: dict[str, Any], history: list[dict[str, Any]]) -> tuple[int, bool]:
    """``(day, gate_met)``: ``day`` counts consecutive qualifying daily runs
    ending with today, capped at 3 for display; ``gate_met`` is true only at
    3. ``today`` has the same shape `_qualifies` reads (`event`/`attempt`/
    `gating_passed`), plus ``date``/``run_id`` (either may be `None`).

    CTO review on #467, must-fix 1: a run whose gating cells actually
    FAILED is day 0 -- not day 1, which would read as progress for a red
    run. must-fix 2: extending past day 1 needs today's own `date` AND a
    history entry whose `date` is EXACTLY one calendar day earlier (not a
    duplicate of today's date, not a gap of more than one day) and whose
    `run_id` is distinct from every run_id already counted in this streak
    -- a pasted-twice entry or a run a week apart no longer passes."""
    if today.get("gating_passed") is not True:
        return 0, False
    if not _qualifies(today) or today.get("date") is None:
        # clean today, but it cannot anchor or extend a streak: no --event,
        # an attempt > 1 somewhere, or no --date to check contiguity with
        return 1, False
    seen_run_ids = {today["run_id"]} if today.get("run_id") else set()
    expected_date = today["date"]
    day = 1
    for entry in reversed(history):
        if day >= 3:
            break
        expected_date = _previous_date(expected_date)
        if not _qualifies(entry):
            break
        if entry.get("date") != expected_date:
            break
        run_id = entry.get("run_id")
        if not run_id or run_id in seen_run_ids:
            break
        seen_run_ids.add(run_id)
        day += 1
    return day, day >= 3


def _previous_date(iso_date: str) -> str:
    return (date_cls.fromisoformat(iso_date) - timedelta(days=1)).isoformat()


def _render_versions(versions: dict[str, Any]) -> str:
    if not versions:
        return "<p>no versions recorded.</p>"
    rows = []
    for pkg, info in versions.items():
        info = info if isinstance(info, dict) else {}
        rows.append(
            "<tr><td>{pkg}</td><td>{repo}</td><td>{version}</td><td><code>{sha}</code></td>"
            "<td>{filename}</td><td><code>{sha256}</code></td></tr>".format(
                pkg=html.escape(str(pkg)),
                repo=html.escape(str(info.get("repo", ""))),
                version=html.escape(str(info.get("version", ""))),
                sha=html.escape(str(info.get("sha", ""))),
                filename=html.escape(str(info.get("filename", ""))),
                sha256=html.escape(str(info.get("sha256", ""))),
            )
        )
    return ("<table class=\"versions\"><tr><th>package</th><th>repo</th><th>version</th>"
            "<th>sha</th><th>filename</th><th>sha256</th></tr>"
            + "".join(rows) + "</table>")


def _render_checks(cell_slug: str, checks: list[dict[str, Any]]) -> str:
    if not checks:
        return "<p>no checks recorded.</p>"
    rows = []
    for check in checks:
        check_id = str(check.get("id", ""))
        result = str(check.get("result", ""))
        log = str(check.get("log", ""))
        log_id = html.escape(f"log-{cell_slug}-{_slug(check_id)}")
        rows.append(
            "<tr class=\"check-{result}\"><td>{id}</td><td>{result}</td>"
            "<td><a href=\"#{log_id}\">log</a><pre id=\"{log_id}\">{log}</pre></td>"
            "</tr>".format(id=html.escape(check_id), result=html.escape(result),
                          log_id=log_id, log=html.escape(log))
        )
    return ("<table class=\"checks\"><tr><th>check</th><th>result</th><th>log</th></tr>"
            + "".join(rows) + "</table>")


def _render_cell(cell: dict[str, Any]) -> str:
    slug = _slug(cell["name"])
    heading_name = html.escape(cell["name"])
    badge = "" if cell["gating"] else ' <span class="badge-non-gating">non-gating (macOS)</span>'

    if cell["status"] == STATUS_MISSING:
        return (f"<section class=\"cell cell-missing\" id=\"cell-{slug}\">"
                f"<h2>{heading_name} <span class=\"status\">missing</span>{badge}</h2>"
                f"<p>no evidence.json found under this cell, or it could not be read as one."
                f"</p></section>")

    os_label = html.escape(str(cell["os"]))
    python_label = html.escape(str(cell["python"]))
    run_id_label = html.escape(str(cell["run_id"] if cell["run_id"] is not None else "n/a"))
    run_attempt = cell["run_attempt"]
    run_attempt_label = html.escape(str(run_attempt if run_attempt is not None else "n/a"))
    retry_note = ""
    if _is_retry(run_attempt):
        retry_note = (f'<p class="retry-flag">attempt {run_attempt_label}: '
                     f'green only on retry, does not count.</p>')

    return (f"<section class=\"cell cell-{cell['status']}\" id=\"cell-{slug}\">"
            f"<h2>{heading_name}: {os_label} / python {python_label} "
            f"<span class=\"status\">{cell['status']}</span>{badge}</h2>"
            f"<p class=\"meta\">run {run_id_label}, attempt {run_attempt_label}</p>"
            + retry_note
            + _render_versions(cell["versions"]) + _render_checks(slug, cell["checks"])
            + "</section>")


def _render_header(cells: list[dict[str, Any]]) -> str:
    # CTO review on #467 round 3 nit: the first cell with a run_id used to
    # stand in for the whole run, even when cells came from different runs
    # (a stale artifact, a partial re-run) -- say "mixed" instead of
    # picking one arbitrarily.
    run_ids = {c["run_id"] for c in cells if c.get("run_id")}
    if len(run_ids) == 1:
        run_id_label = html.escape(str(next(iter(run_ids))))
    elif run_ids:
        run_id_label = "mixed"
    else:
        run_id_label = "n/a"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f'<p class="run-header">Run <strong>{run_id_label}</strong> &mdash; '
            f'generated {generated}. Gate rule: <em>{html.escape(GATE_RULE)}</em></p>')


def _render_summary(counts: dict[str, Any], day: int, gate_met: bool,
                    expect_gating: int | None = None) -> str:
    gating = counts["gating"]
    # shal#402: "gate met" is produced from the 3-daily-run check alone
    # (`gate_status`, via `day`/`gate_met` here) -- never from this run's
    # own cell counts. CTO review on #467 nit: keep the "this run: N/M"
    # line even when the gate is met, so the page never drops this run's
    # own numbers just because the streak reached 3.
    #
    # round 4 nit: when the count does not match `--expect-gating`, say so
    # -- otherwise a reader sees "day 0 of 3" with no clue why a run that
    # looks all-green isn't day 1.
    expected = (f" (expected {expect_gating})"
               if expect_gating is not None and gating["cells"] != expect_gating else "")
    this_run = (f"this run: {gating['passed']}/{gating['cells']}{expected} gating cells "
               f"pass (day {day} of 3)")
    verdict = f"{this_run} — gate met" if gate_met else this_run
    return (
        "<table class=\"summary\">"
        "<tr><th></th><th>cells</th><th>passed</th><th>failed</th><th>missing</th></tr>"
        "<tr><td>all</td><td>{cells}</td><td>{passed}</td><td>{failed}</td><td>{missing}</td></tr>"
        "<tr><td>gating (non-macOS)</td><td>{g_cells}</td><td>{g_passed}</td>"
        "<td>{g_failed}</td><td>{g_missing}</td></tr>"
        "</table><p class=\"gate-verdict\">{verdict}</p>"
    ).format(
        cells=counts["cells"], passed=counts["passed"], failed=counts["failed"],
        missing=counts["missing"], g_cells=gating["cells"], g_passed=gating["passed"],
        g_failed=gating["failed"], g_missing=gating["missing"],
        verdict=verdict,
    )


_STYLE = """
body { font-family: system-ui, sans-serif; margin: 2rem; color: #111; }
table { border-collapse: collapse; margin: 0.5rem 0 1.5rem; width: 100%; }
th, td { border: 1px solid #ccc; padding: 0.3rem 0.6rem; text-align: left;
         vertical-align: top; }
section.cell { border: 1px solid #999; border-radius: 4px; padding: 1rem;
               margin-bottom: 1.5rem; }
.cell-pass h2 .status { color: #0a7a2a; }
.cell-fail h2 .status, .cell-missing h2 .status { color: #b00020; }
tr.check-fail { background: #fdecea; }
tr.check-pass { background: #eaf7ec; }
pre { white-space: pre-wrap; word-break: break-word; margin: 0.3rem 0 0; }
.badge-non-gating { color: #8a6d00; font-size: 0.8em; border: 1px solid #d8c060;
                    border-radius: 3px; padding: 0.1rem 0.4rem; }
.retry-flag { color: #8a6d00; font-weight: bold; }
"""


def _is_attempt_one(value: Any) -> bool:
    """CTO review on #467 round 3, must-fix 1: `dev/e2e/story.py` writes
    `run_attempt` from `GITHUB_RUN_ATTEMPT`, an environment variable -- so a
    real cell's value is always the STRING `"1"`, never the int `1`. The
    round-2 `type(...) is int` check rejected every real cell, stuck at day
    1 for good. Accept exactly the int `1` (bool excluded, as `bool` is an
    `int` subclass) or the exact string `"1"`; reject anything else,
    including `"2"`, `"unknown"`, `True` and `None`."""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 1
    return value == "1"


def today_entry(cells: list[dict[str, Any]], counts: dict[str, Any], *,
                event: str | None, date: str | None,
                expect_gating: int | None = None) -> dict[str, Any]:
    """This run, in the same shape a history entry takes -- CTO review on
    #467 round 1, must-fix 1: today is held to the SAME 3 conditions
    (`event`=="schedule", every gating cell's own `run_attempt` == 1,
    every gating cell green), not just its cell counts.

    Round 2 closes 3 more ways to fool it, found after round 1 shipped:
    must-fix 1: `gating["cells"] == 0` (an empty evidence dir, or macOS-only
    cells) used to count as "all green" -- a run with NO gating cells now
    never passes. must-fix 2: a missing or non-int `run_attempt` used to
    read as attempt 1 (`_is_retry(None)` is False) -- only a REAL `int 1`
    counts now; `_is_retry` itself is untouched, since the page's own retry
    flag on a single cell still wants "not literally > 1". must-fix 3:
    `run_id` was the first cell's that had one, with the rest never
    compared -- cells from different runs (a stale artifact, a partial
    re-run) could be stitched into one false "today". Every gating cell
    must now share the SAME non-empty `run_id`, or today cannot anchor a
    streak (folded into `attempt`, same mechanism a real retry already
    uses -- not a second check gate_status would need to know about).

    Round 3 (CTO review on PR #467) closes 2 more: must-fix 1 above, read
    through `_is_attempt_one`; and `expect_gating` (`--expect-gating`, round
    3 must-fix 2b) -- without a known matrix size, a partial download (only
    some of the real gating cells present, the rest never fetched) still
    reads as "all of them passed". `run_id == "unknown"` (`story.py`'s own
    value outside CI, round 3 nit) is excluded from consistency the same
    way a missing run_id already was -- it must never anchor a streak.

    Round 4 (CTO review on PR #467) must-fix 1: `--expect-gating` being
    OPTIONAL left the unsafe answer as the default -- a caller that forgets
    the flag got a false "gate met" on a partial download, the exact
    failure #402 exists to stop. `expect_gating is not None` now folds into
    `attempt` the same way `attempt_ok`/`run_id_consistent` already do:
    with no `--expect-gating` at all, today can anchor or extend a streak
    no further than day 1, same as a missing `--event`/`--date`."""
    gating = counts["gating"]
    gating_cells = [c for c in cells if c["gating"]]
    gating_count_ok = gating["cells"] > 0 and (
        expect_gating is None or gating["cells"] == expect_gating)
    gating_passed = gating_count_ok and gating["passed"] == gating["cells"]
    attempt_ok = all(_is_attempt_one(c["run_attempt"]) for c in gating_cells)
    run_ids = [c.get("run_id") for c in gating_cells]
    run_id_consistent = bool(run_ids) and all(
        r and r != "unknown" and r == run_ids[0] for r in run_ids)
    run_id = run_ids[0] if run_id_consistent else None
    attempt = 1 if (attempt_ok and run_id_consistent and expect_gating is not None) else 2
    return {"event": event, "attempt": attempt, "gating_passed": gating_passed,
           "date": date, "run_id": run_id}


def render_page(cells: list[dict[str, Any]], day: int, gate_met: bool,
                expect_gating: int | None = None) -> str:
    counts = summarize(cells)
    header = _render_header(cells)
    summary = _render_summary(counts, day, gate_met, expect_gating)
    body = "".join(_render_cell(c) for c in cells)
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>Evidence — clean-machine run</title>"
            f"<style>{_STYLE}</style></head><body>"
            f"<h1>Evidence — clean-machine run</h1>{header}{summary}{body}</body></html>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "dir", type=Path, help="directory holding one subdirectory per cell, each with its "
                               "own evidence.json (gh run download's own layout)")
    parser.add_argument("--out", required=True, type=Path, metavar="PATH",
                        help="where to write the HTML page")
    parser.add_argument("--history", type=Path, metavar="FILE", default=None,
                        help="JSON list of the runs immediately before this one, oldest "
                             f"first ({HISTORY_FORMAT}) -- must NOT include this run itself "
                             "(a workflow that appends today's own run first stays on day 1 "
                             "for good); without --history, this run is always day 1 of 3 "
                             "and the gate is never met from one run (shal#402)")
    parser.add_argument("--expect-gating", type=int, default=None, metavar="N",
                        help="the number of non-macOS cells the matrix is expected to "
                             "have -- without it, a partial download (fewer cells than the "
                             "real matrix) can still read as 'all of them passed'; with it, "
                             "today's own gating cell count must equal N (shal#402 round 3)")
    parser.add_argument("--event", default=None, metavar="NAME",
                        help="this run's own trigger, e.g. the workflow's "
                             "${{ github.event_name }} -- without it, today can never "
                             "anchor or extend a streak past day 1 (shal#402 round 2)")
    parser.add_argument("--date", default=None, metavar="YYYY-MM-DD",
                        help="this run's own UTC date -- never guessed from the clock, so "
                             "the gate stays deterministic; without it, today can never "
                             "extend a streak past day 1 (shal#402 round 2)")
    parser.add_argument("--json", action="store_true", help="print the cell counts as JSON")
    args = parser.parse_args(argv)

    if args.date is not None and not _is_iso_date(args.date):
        print(f'evidence_page.py: --date must be "YYYY-MM-DD", got {args.date!r}',
             file=sys.stderr)
        return 2

    if args.expect_gating is not None and args.expect_gating < 1:
        print(f"evidence_page.py: --expect-gating must be >= 1, got {args.expect_gating}",
             file=sys.stderr)
        return 2

    history: list[dict[str, Any]] = []
    if args.history is not None:
        try:
            history = load_history(args.history)
        except ValueError as e:
            print(f"evidence_page.py: {e}", file=sys.stderr)
            return 2

    cells = load_cells(args.dir)
    counts = summarize(cells)
    gating = counts["gating"]
    day, gate_met = gate_status(
        today_entry(cells, counts, event=args.event, date=args.date,
                   expect_gating=args.expect_gating), history)
    args.out.write_text(render_page(cells, day, gate_met, args.expect_gating), encoding="utf-8")

    if args.json:
        print(json.dumps({**counts, "day": day, "gate_met": gate_met,
                          "page": str(args.out), "side_effect": "write"}))
    else:
        verdict = f"day {day} of 3 — gate met" if gate_met else f"day {day} of 3"
        print(f"evidence page written to {args.out}: {counts['cells']} cell(s), "
             f"{counts['passed']} passed, {counts['failed']} failed, "
             f"{counts['missing']} missing (gating: {gating['passed']} passed, "
             f"{gating['failed']} failed, {gating['missing']} missing; {verdict})")

    return 1 if gating["failed"] or gating["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
