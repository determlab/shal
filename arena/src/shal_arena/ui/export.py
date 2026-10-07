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

import html
import time
from pathlib import Path
from typing import Any

from ..errors import ArenaError
from ..store import DEFAULT_STATE_DIR
from .data import run_payload
from .page import _SCRIPT, _shell, safe_json


class RunNotFinished(ArenaError):
    """`--export` only ever plays back a closed run -- same rule, same
    message shape as `replay/card.py`'s own (issue #315): nothing here can
    build an export with the fault in it before the run has one."""

    exit_code = 2


_EXPORT_SCRIPT = r"""
function startExport(fullPayload) {
  // issue #406 CTO review: mode is always "replay" here, never "live" --
  // withholding closed/record/score mid-animation must not also flip the
  // badge, which is never LIVE for an export. issue #481: one tick per
  // row the timeline really shows (`shownSteps`, in the page script) -- an exchange
  // row or a measure its reading later completes never costs a tick, and
  // never shows on its own.
  const steps = shownSteps(fullPayload.timeline);
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


def _label(agent: str | None, date: str, seed: int | None) -> str:
    # issue #406 CTO review: --agent and the record's own closed_at both go
    # into the HTML as text, not data -- html.escape, not safe_json (which
    # only protects a `</script>` breakout, not a bare `<tag>`).
    safe_agent = html.escape(agent or "agent")
    safe_date = html.escape(date)
    # issue #427 CMO review: the replayed-run line names the seed -- the
    # public part of a run's identity, written to the state file before
    # the run even closes (never the hidden fault).
    # issue #447 page text: the footer reads "Replay of a recorded run ·
    # <agent>, <date>, seed <n>", after the fixed simulated-instruments line.
    seed_part = f", seed {seed}" if seed is not None else ""
    return f"Replay of a recorded run · {safe_agent}, {safe_date}{seed_part}"


def render_export_page(payload: dict[str, Any], *, agent: str | None = None) -> str:
    """`payload` must already be a CLOSED run's (`run_payload`'s own
    `closed: True`) -- `build_export` is the only caller, and it refuses
    first (`RunNotFinished`), so this never has to re-check."""
    date = (payload.get("record") or {}).get("closed_at", time.strftime(
        "%Y-%m-%dT%H:%M:%SZ", time.gmtime()))[:10]
    label = _label(agent, date, payload.get("seed"))
    payload_json = safe_json(payload)
    shell = _shell(payload["run_id"], banner=label)
    script_tag = (
        f'<script id="run-data" type="application/json">{payload_json}</script>\n'
        f"<script>{_SCRIPT}\n{_EXPORT_SCRIPT}\n"
        f"startExport(withLog(JSON.parse(document.getElementById('run-data').textContent)));"
        f"</script>\n"
    )
    return shell.replace("</body>", script_tag + "</body>")


def build_export(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR,
                 agent: str | None = None,
                 drivers: dict[str, dict[str, Any]] | None = None) -> str:
    payload = run_payload(run_id, state_dir=state_dir)
    if not payload["closed"]:
        raise RunNotFinished(
            f"run {run_id!r} is not finished yet; export plays back a closed run only",
            fix=f"close it first: shal-arena answer {run_id} <value> --json")
    payload["drivers"] = drivers or {}
    return render_export_page(payload, agent=agent)
