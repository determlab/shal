"""Result card & replay (issue #315 Scope): one local HTML file, made at the
end of a run, reading only the run record (`<run_id>.arena-record.json`) and score
file (`<run_id>.score.json`) — the same two files the issue's Agent path
tells an agent to read directly with `--json`, never a third source and
never anything added. Works offline: no outside fonts or scripts, and the
only network reference anywhere in the page is the `github.com/determlab/shal`
link `_card_html` renders (`test_card_page.py` greps for exactly that).

DoD 4 ("no hint of the fault before the run ends") holds structurally here,
not by a redaction pass: `load_card_data` reads `<run_id>.arena-record.json` and
`<run_id>.score.json`, and neither file exists until `shal-arena answer`
closes the run and writes them (`runner.answer`, `store.answer`) — so there
is no "early" card this module could ever build with the fault in it. Call
it before the run has closed and it raises `RunNotFinished`, naming the fix,
same as every other arena error.

The replay itself plays back what the sim log actually recorded reaching the
sim (issue #312's `simlog.SimLog`: `measure` markers, successful `query`/
`write` exchanges, and the card's own `protection`/`damage`/`refused` lines
from `drive_input` — issue #313) merged with the public run state's `check`
tile results (`store.RunState.tiles`, each carrying its own `checked_at`),
sorted by timestamp. `check-driver` itself never touches the sim log by
design (CTO review on #322: it would double-credit a measurement for a call
every player makes just to light the tile) — this is why the tile states are
merged in alongside the sim log rather than read from it. The turn count the
card shows is `score["turns"]`, the one field that already counts
`check`+`measure`+`drive` the way the CTO ruling on #314 fixed (a refused or
otherwise-failing `drive` still costs its turn — `store.increment_turns`
runs before the call itself, issue #325) and leaves `answer` uncounted;
nothing in this module recomputes that count a second way.
"""
from __future__ import annotations

import html
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import ArenaError
from ..simlog import SimLog
from ..store import DEFAULT_STATE_DIR, RunStore

REPO_URL = "https://github.com/determlab/shal"
_CARD_WIDTH = 1200
_CARD_HEIGHT = 630


class RunNotFinished(ArenaError):
    """No record/score file yet for this run (issue #315 Scope: "no hint of
    the fault before the run ends") — `shal-arena answer` has not closed it
    yet, so there is nothing for this module to safely read."""

    exit_code = 2


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class CardData:
    run_id: str
    record: dict[str, Any]
    score: dict[str, Any]
    timeline: list[dict[str, Any]]


def _timeline(run_id: str, store: RunStore) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    sim_log = SimLog(store.sim_log_path(run_id))
    for e in sim_log.entries():
        entries.append({
            "ts": e["ts"], "kind": e["kind"], "address": e.get("address"),
            "detail": {k: v for k, v in e.items() if k not in ("ts", "kind", "address")},
        })
    state = store.load(run_id)
    for address, tile in state.tiles.items():
        entries.append({
            "ts": tile.checked_at, "kind": "check", "address": address,
            "detail": {"case": tile.case, "passed": tile.passed},
        })
    # ISO "%Y-%m-%dT%H:%M:%SZ" timestamps sort correctly as plain strings.
    entries.sort(key=lambda e: e["ts"])
    return entries


def load_card_data(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR) -> CardData:
    """Read exactly the two files issue #315's Agent path names
    (`<run_id>.arena-record.json`, `<run_id>.score.json`), plus the sim log and the
    public run state only to reconstruct the replay timeline — never
    anything that could carry the fault before this run closed."""
    store = RunStore(state_dir)
    record_path = store.record_path(run_id)
    score_path = store.score_path(run_id)
    if not record_path.is_file() or not score_path.is_file():
        raise RunNotFinished(
            f"run {run_id!r} has no record/score yet; the result card needs a closed run",
            fix=f"close the run first: shal-arena answer {run_id} <value> --json")
    record = _load_json(record_path)
    score = _load_json(score_path)
    timeline = _timeline(run_id, store)
    return CardData(run_id=run_id, record=record, score=score, timeline=timeline)


def _short_sha(sha256: str) -> str:
    return sha256[:12] + "…"  # ellipsis — shortened, never the full id


def _headline(score: dict[str, Any]) -> str:
    caught, total = score["faults_caught"], score["faults_total"]
    false_fails = score["false_fails"]
    noun = "false fail" if false_fails == 1 else "false fails"
    return f"{caught} of {total} faults caught. {false_fails} {noun}."


def _bottom_line(score: dict[str, Any]) -> str:
    return f"replay · simulated · seed {score['seed']}"


_SAFETY_LINE = "Simulated instruments only. Nothing here touches real hardware."
_GATE_STOP_SENTENCE = "Stopped by the gate before it ran. Nothing was changed."


_KIND_LABEL = {
    "check": "check", "measure": "measure", "query": "read", "write": "write",
    "protection": "drive (protection)", "damage": "drive (damage)",
    "refused": "drive — refused",
}


def _timeline_row_html(entry: dict[str, Any]) -> str:
    kind = entry["kind"]
    label = html.escape(_KIND_LABEL.get(kind, kind))
    address = html.escape(str(entry.get("address") or ""))
    css_class = "row-refused" if kind == "refused" else f"row-{kind}"
    gate_note_html = ""
    if kind == "check":
        result = "pass" if entry["detail"].get("passed") else "fail"
        css_class = "row-check-pass" if entry["detail"].get("passed") else "row-check-fail"
    elif kind == "refused":
        result = "refused"
        # the gate stopped a would-be `drive_input` write (issue #330); never
        # shown for a bad-argument error, which raises before any sim-log
        # line is written and so never reaches this row at all.
        gate_note_html = f'<br><span class="gate-note">{html.escape(_GATE_STOP_SENTENCE)}</span>'
    elif kind in ("protection", "damage"):
        result = kind
    else:
        result = "ok"
    return (
        f'<tr class="{css_class}"><td>{html.escape(entry["ts"])}</td>'
        f"<td>{label}</td><td>{address}</td><td>{html.escape(result)}{gate_note_html}</td></tr>"
    )


def _timeline_html(timeline: list[dict[str, Any]]) -> str:
    if not timeline:
        return "<p>No calls were logged for this run.</p>"
    rows = "\n".join(_timeline_row_html(e) for e in timeline)
    return (
        "<table class=\"timeline\"><thead><tr><th>time</th><th>call</th>"
        f"<th>address</th><th>result</th></tr></thead><tbody>{rows}</tbody></table>"
    )


def render_card(data: CardData) -> str:
    """The result card: one offline HTML file. Labelled a replay, states the
    headline and the `false_fails` count openly whatever its value, plays
    back the run's calls (a gate-refused call in red), and ends its one
    "Copy result" string with `github.com/determlab/shal` — the only network
    reference anywhere on the page."""
    score = data.score
    headline = _headline(score)
    bottom_line = _bottom_line(score)
    turns = score["turns"]
    fault_type = html.escape(score["fault_type"])
    game_version = html.escape(score["game_version"])
    short_sha = html.escape(_short_sha(score["record_sha256"]))
    copy_text = f"{headline} {bottom_line} — github.com/determlab/shal"
    copy_text_json = json.dumps(copy_text)
    timeline_html = _timeline_html(data.timeline)
    disqualified_html = (
        '<p class="disqualified">Disqualified: no measurement was logged for this run.</p>'
        if data.record.get("disqualified") else ""
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SHAL Arena — result card</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 24px; background: #0b0f14; color: #e6edf3; }}
  .badge {{ display: inline-block; font-size: 11px; letter-spacing: 0.08em;
           text-transform: uppercase; padding: 2px 8px; border-radius: 4px;
           background: #30363d; color: #e6edf3; margin-bottom: 8px; }}
  h1 {{ font-size: 22px; margin: 4px 0; }}
  .bottom-line {{ color: #8b949e; font-size: 13px; margin-bottom: 4px; }}
  .meta-line {{ color: #8b949e; font-size: 12px; margin-bottom: 16px; }}
  .disqualified {{ color: #f85149; font-weight: 600; }}
  table.timeline {{ border-collapse: collapse; font-size: 13px; width: 100%; max-width: 720px; }}
  table.timeline th, table.timeline td {{ border-bottom: 1px solid #30363d;
                                          padding: 4px 8px; text-align: left; }}
  .row-refused, .row-check-fail {{ color: #f85149; }}
  button {{ margin-right: 8px; margin-top: 12px; }}
  #card-canvas {{ display: none; }}
</style>
</head>
<body>
<div class="badge">Replay</div>
<h1>{html.escape(headline)}</h1>
<div class="bottom-line">{html.escape(bottom_line)}</div>
<div class="safety-line">{html.escape(_SAFETY_LINE)}</div>
<div class="meta-line">fault: {fault_type} · game {game_version} · turns {turns} ·
record {short_sha}</div>
{disqualified_html}

<h2>Replay</h2>
{timeline_html}

<div>
  <button id="copy-btn" type="button">Copy result</button>
  <button id="save-image-btn" type="button">Save as image</button>
</div>
<p><a id="repo-link" href="{REPO_URL}">github.com/determlab/shal</a></p>

<canvas id="card-canvas" width="{_CARD_WIDTH}" height="{_CARD_HEIGHT}"></canvas>

<script>
const COPY_TEXT = {copy_text_json};
const HEADLINE = {json.dumps(headline)};
const BOTTOM_LINE = {json.dumps(bottom_line)};

document.getElementById('copy-btn').addEventListener('click', () => {{
  navigator.clipboard.writeText(COPY_TEXT);
}});

document.getElementById('save-image-btn').addEventListener('click', () => {{
  const canvas = document.getElementById('card-canvas');
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#0b0f14';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#e6edf3';
  ctx.font = '600 40px -apple-system, sans-serif';
  ctx.fillText(HEADLINE, 60, 260, canvas.width - 120);
  ctx.font = '24px -apple-system, sans-serif';
  ctx.fillStyle = '#8b949e';
  ctx.fillText(BOTTOM_LINE, 60, 320, canvas.width - 120);
  canvas.toBlob(blob => {{
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'result-card.png';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }});
}});
</script>
</body>
</html>
"""


def build_result_card(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR) -> str:
    """`load_card_data` + `render_card` in one call — what `shal-arena
    replay` writes to disk."""
    return render_card(load_card_data(run_id, state_dir=state_dir))
