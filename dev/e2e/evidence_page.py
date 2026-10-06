#!/usr/bin/env python
"""Evidence page (shal#380): one static HTML page built from the evidence.json
files of a clean-machine run (dev/e2e/story.py, workflow
.github/workflows/e2e-clean-machine.yml).

Each matrix cell (one OS x one Python) uploads its own `evidence-<os>-<py>`
artifact holding a single evidence.json: `os`, `python`, `versions` (one entry
per package: version, sha, repo, filename, sha256) and `checks[]` (`id`,
`result`, `log`). After `gh run download <run-id> -D ev`, `ev/` holds one
subdirectory per artifact. This script reads every subdirectory of the
directory it is given, writes one HTML page listing every cell's versions and
checks, and prints a count of how many cells passed, failed, or were missing.

A cell is `missing` when its evidence.json is absent or cannot be parsed as
the shape above -- never silently skipped, so a reviewer (or an agent reading
`--json`) sees it just like a failed one.

Usage:
    evidence_page.py DIR --out evidence.html [--json]

Exit 0 if every cell passed, 1 if any check failed or any cell is missing.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_MISSING = "missing"


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", text).strip("-")


def _empty_cell(name: str) -> dict[str, Any]:
    return {"name": name, "status": STATUS_MISSING, "os": None, "python": None,
            "versions": {}, "checks": []}


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
    return {"name": name, "status": status, "os": doc.get("os"), "python": doc.get("python"),
            "versions": doc.get("versions") or {}, "checks": checks}


def load_cells(root: Path) -> list[dict[str, Any]]:
    return [_load_cell(p) for p in sorted(root.iterdir()) if p.is_dir()]


def summarize(cells: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "cells": len(cells),
        "passed": sum(1 for c in cells if c["status"] == STATUS_PASS),
        "failed": sum(1 for c in cells if c["status"] == STATUS_FAIL),
        "missing": sum(1 for c in cells if c["status"] == STATUS_MISSING),
    }


def _render_versions(versions: dict[str, Any]) -> str:
    if not versions:
        return "<p>no versions recorded.</p>"
    rows = []
    for pkg, info in versions.items():
        info = info if isinstance(info, dict) else {}
        rows.append(
            "<tr><td>{pkg}</td><td>{repo}</td><td>{version}</td><td><code>{sha}</code></td>"
            "</tr>".format(
                pkg=html.escape(str(pkg)),
                repo=html.escape(str(info.get("repo", ""))),
                version=html.escape(str(info.get("version", ""))),
                sha=html.escape(str(info.get("sha", ""))),
            )
        )
    return ("<table class=\"versions\"><tr><th>package</th><th>repo</th><th>version</th>"
            "<th>sha</th></tr>" + "".join(rows) + "</table>")


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
    heading = html.escape(cell["name"])
    if cell["status"] == STATUS_MISSING:
        return (f"<section class=\"cell cell-missing\" id=\"cell-{slug}\">"
                f"<h2>{heading} <span class=\"status\">missing</span></h2>"
                f"<p>no evidence.json found under this cell, or it could not be read as one."
                f"</p></section>")
    os_label = html.escape(str(cell["os"]))
    python_label = html.escape(str(cell["python"]))
    return (f"<section class=\"cell cell-{cell['status']}\" id=\"cell-{slug}\">"
            f"<h2>{heading}: {os_label} / python {python_label} "
            f"<span class=\"status\">{cell['status']}</span></h2>"
            + _render_versions(cell["versions"]) + _render_checks(slug, cell["checks"])
            + "</section>")


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
"""


def render_page(cells: list[dict[str, Any]]) -> str:
    counts = summarize(cells)
    summary = (
        "<table class=\"summary\"><tr><th>cells</th><th>passed</th><th>failed</th>"
        "<th>missing</th></tr><tr><td>{cells}</td><td>{passed}</td><td>{failed}</td>"
        "<td>{missing}</td></tr></table>"
    ).format(**counts)
    body = "".join(_render_cell(c) for c in cells)
    return ("<!doctype html><html><head><meta charset=\"utf-8\">"
            "<title>Evidence — clean-machine run</title>"
            f"<style>{_STYLE}</style></head><body>"
            f"<h1>Evidence — clean-machine run</h1>{summary}{body}</body></html>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "dir", type=Path, help="directory holding one subdirectory per cell, each with its "
                               "own evidence.json (gh run download's own layout)")
    parser.add_argument("--out", required=True, type=Path, metavar="PATH",
                        help="where to write the HTML page")
    parser.add_argument("--json", action="store_true", help="print the cell counts as JSON")
    args = parser.parse_args(argv)

    cells = load_cells(args.dir)
    args.out.write_text(render_page(cells), encoding="utf-8")
    counts = summarize(cells)

    if args.json:
        print(json.dumps({**counts, "page": str(args.out)}))
    else:
        print(f"evidence page written to {args.out}: {counts['cells']} cell(s), "
             f"{counts['passed']} passed, {counts['failed']} failed, "
             f"{counts['missing']} missing")

    return 1 if counts["failed"] or counts["missing"] else 0


if __name__ == "__main__":
    sys.exit(main())
