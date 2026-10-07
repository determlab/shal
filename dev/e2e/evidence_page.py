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
scheduled run, attempt 1, every gating cell green. With no `--history`,
day is always 1 (nothing to look back on) and the gate is never met from a
single run.

Usage:
    evidence_page.py DIR --out evidence.html [--history runs.json] [--json]

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
from datetime import datetime, timezone
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


#: shal#402: the shape --history takes, named in every error about it.
HISTORY_FORMAT = ('a JSON list of prior runs, oldest first, each '
                  '{"event": <str>, "attempt": <int>, "gating_passed": <bool>}')


def load_history(path: Path) -> list[dict[str, Any]]:
    """`--history FILE`: the runs immediately before this one, oldest
    first, ending with the most recent run before this one. Never a
    dashboard of every run ever -- only enough to look back 2 days (the gate
    needs 3 in a row including today). Raises ValueError, naming
    `HISTORY_FORMAT`, for anything that cannot be read as that shape (shal#402
    Agent path: "an error for a bad history file names the expected
    format")."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        raise ValueError(f"--history {path}: cannot read file ({e}); expected "
                         f"{HISTORY_FORMAT}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"--history {path}: not valid JSON ({e}); expected "
                         f"{HISTORY_FORMAT}") from e
    if not isinstance(doc, list):
        raise ValueError(f"--history {path}: expected {HISTORY_FORMAT}, "
                         f"got {type(doc).__name__}")
    for entry in doc:
        if not (isinstance(entry, dict) and {"event", "attempt", "gating_passed"} <= entry.keys()):
            raise ValueError(f"--history {path}: every entry must have "
                             f"event/attempt/gating_passed; expected {HISTORY_FORMAT}")
    return doc


def _qualifies(entry: dict[str, Any]) -> bool:
    """One history entry counts towards the streak only if it was a
    scheduled run (not a manual/PR run), green on the first attempt, and
    every gating cell passed -- the exact 3 conditions #402's gate rule
    names."""
    return (entry.get("event") == "schedule" and entry.get("attempt") == 1
            and bool(entry.get("gating_passed")))


def gate_status(today_gating_passed: bool, history: list[dict[str, Any]]) -> tuple[int, bool]:
    """``(day, gate_met)``: ``day`` counts consecutive qualifying daily runs
    ending with today, capped at 3 for display; ``gate_met`` is true only at
    3. A single failing or non-qualifying day anywhere in the 3 (today, or
    either of the 2 history entries immediately before it) resets the count
    -- it never partially credits a streak that broke."""
    if not today_gating_passed:
        return 1, False
    streak = 1
    for entry in reversed(history):
        if streak >= 3:
            break
        if not _qualifies(entry):
            break
        streak += 1
    return streak, streak >= 3


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
    run_id = next((c["run_id"] for c in cells if c.get("run_id")), None)
    run_id_label = html.escape(str(run_id)) if run_id else "n/a"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return (f'<p class="run-header">Run <strong>{run_id_label}</strong> &mdash; '
            f'generated {generated}. Gate rule: <em>{html.escape(GATE_RULE)}</em></p>')


def _render_summary(counts: dict[str, Any], day: int, gate_met: bool) -> str:
    gating = counts["gating"]
    # shal#402: "gate met" is produced from the 3-daily-run check alone
    # (`gate_status`, via `day`/`gate_met` here) -- never from this run's
    # own cell counts, which is what the one-run verdict below reports
    # instead.
    verdict = ("gate met" if gate_met else
              f"this run: {gating['passed']}/{gating['cells']} gating cells "
              f"pass (day {day} of 3)")
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


def render_page(cells: list[dict[str, Any]],
                history: list[dict[str, Any]] | None = None) -> str:
    counts = summarize(cells)
    gating = counts["gating"]
    today_gating_passed = gating["failed"] == 0 and gating["missing"] == 0
    day, gate_met = gate_status(today_gating_passed, history or [])
    header = _render_header(cells)
    summary = _render_summary(counts, day, gate_met)
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
                             f"first ({HISTORY_FORMAT}) -- without it, this run is always "
                             "day 1 of 3 and the gate is never met from one run (shal#402)")
    parser.add_argument("--json", action="store_true", help="print the cell counts as JSON")
    args = parser.parse_args(argv)

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
    today_gating_passed = gating["failed"] == 0 and gating["missing"] == 0
    day, gate_met = gate_status(today_gating_passed, history)
    args.out.write_text(render_page(cells, history), encoding="utf-8")

    if args.json:
        print(json.dumps({**counts, "day": day, "gate_met": gate_met,
                          "page": str(args.out), "side_effect": "write"}))
    else:
        verdict = "gate met" if gate_met else f"day {day} of 3"
        print(f"evidence page written to {args.out}: {counts['cells']} cell(s), "
             f"{counts['passed']} passed, {counts['failed']} failed, "
             f"{counts['missing']} missing (gating: {gating['passed']} passed, "
             f"{gating['failed']} failed, {gating['missing']} missing; {verdict})")

    return 1 if gating["failed"] or gating["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
