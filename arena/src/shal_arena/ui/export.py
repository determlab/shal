"""`shal-arena ui --export` (issue #406): one self-contained HTML file for a
FINISHED run, replaying its timeline as an animation -- inline CSS and JS,
no network, reusing every rendering function `page.py` already has
(`_SCRIPT`). The one thing export does that the live page does not: it
withholds `record`/`score`/`closed` from every frame but the last, so the
verdict and result card -- which exist in the data from the moment this
file is written, since the run is already finished -- are never what the
viewer sees until the animation itself reaches the end.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from ..errors import ArenaError
from ..store import DEFAULT_STATE_DIR
from .data import run_payload
from .page import _BENCH_SVG, _SCRIPT, _STYLE, REPO_URL, SAFETY_LINE  # noqa: F401 - reused verbatim


class RunNotFinished(ArenaError):
    """`--export` only ever plays back a closed run -- same rule, same
    message shape as `replay/card.py`'s own (issue #315): nothing here can
    build an export with the fault in it before the run has one."""

    exit_code = 2


_EXPORT_SCRIPT = r"""
function startExport(fullPayload) {
  // issue #406 CTO review: mode is always "replay" here, never "live" --
  // withholding closed/record/score mid-animation must not also flip the
  // badge, which is never LIVE for an export.
  const steps = fullPayload.timeline;
  let i = 0;
  function tick() {
    if (i < steps.length) {
      render(Object.assign({}, fullPayload, {closed: false, record: null, score: null,
        mode: "replay", timeline: steps.slice(0, i + 1)}));
      i++;
      setTimeout(tick, 900);
    } else {
      render(Object.assign({}, fullPayload, {mode: "replay"}));   // verdict + result, only now
    }
  }
  render(Object.assign({}, fullPayload, {closed: false, record: null, score: null,
    mode: "replay", timeline: []}));
  setTimeout(tick, 900);
}
"""


def _label(agent: str | None, date: str) -> str:
    return f"Replay of a recorded run · {agent or 'agent'} · {date} · simulated instruments"


def render_export_page(payload: dict[str, Any], *, agent: str | None = None) -> str:
    """`payload` must already be a CLOSED run's (`run_payload`'s own
    `closed: True`) -- `build_export` is the only caller, and it refuses
    first (`RunNotFinished`), so this never has to re-check."""
    date = (payload.get("record") or {}).get("closed_at", time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))[:10]
    label = _label(agent, date)
    payload_json = json.dumps(payload)
    body = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shal-arena &middot; Watch replay</title>
<style>{_STYLE}
.export-label {{ background: var(--panel); color: var(--text); font-size: 13px;
  font-weight: 700; text-align: center; padding: 10px 16px; border-bottom: 1px solid var(--line); }}
</style>
</head>
<body>
<div class="export-label">{label}</div>
<div class="wrap">
  <div class="badge-row" id="badge-row"><span class="dot"></span> Replay of a recorded run</div>
  <header>
    <div class="title" id="title"></div>
    <div class="sub" id="sub"></div>
  </header>
  <div class="verdict-bar" id="verdict-bar"></div>
  <div class="section-label">Bench</div>
  <div class="bench">{_BENCH_SVG}</div>
  <div class="section-label">Timeline</div>
  <div class="timeline" id="timeline-list"></div>
  <div id="result-section"></div>
  <footer>{SAFETY_LINE}
    &middot; <span class="run-id mono" id="run-id"></span>
    &middot; <a href="{REPO_URL}">github.com/determlab/shal</a>
  </footer>
</div>
<script id="run-data" type="application/json">{payload_json}</script>
<script>{_SCRIPT}
{_EXPORT_SCRIPT}
startExport(JSON.parse(document.getElementById('run-data').textContent));</script>
</body>
</html>
"""
    return body


def build_export(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR,
                 agent: str | None = None) -> str:
    payload = run_payload(run_id, state_dir=state_dir)
    if not payload["closed"]:
        raise RunNotFinished(
            f"run {run_id!r} is not finished yet; export plays back a closed run only",
            fix=f"close it first: shal-arena answer {run_id} <value> --json")
    return render_export_page(payload, agent=agent)
