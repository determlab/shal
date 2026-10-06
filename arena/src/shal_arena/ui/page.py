"""The Watch page itself (issue #406): one static document, styled per the
CTO-approved mock (`ui/mock/watch.html`) -- dark theme, brand tokens, a real
SVG bench, a verdict bar above the fold, no raw JSON on screen. The
rendering logic (`_SCRIPT`) is plain JavaScript, inline, no network beyond
`/api/run/<id>` on the same origin -- same rule the result card already
holds (`replay/card.py`): the only outside reference anywhere is the repo
link.

`render_watch_page` embeds the run's current payload once, as data (never as
pre-rendered fault text), inside a `<script type="application/json">` tag;
the page's own JS renders from that on load, then polls `/api/run/<id>`
every second until the run closes. `render_export_page` (export.py) reuses
every function below except the polling loop -- a finished run's full
payload, played back as an animation instead of live-polled.
"""
from __future__ import annotations

import json
from typing import Any

REPO_URL = "https://github.com/determlab/shal"

#: CTO brand tokens (#406 round 2 approval) -- local stacks only, no network.
_STYLE = """
:root {
  --bg: #1A1D23; --panel: #262B33; --text: #F2F4F7; --dim: #9BA3AF;
  --gate: #FFB224; --ok: #3DDC84; --stop: #F0524D; --line: #343B46;
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
  font-family: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  font-size: 14px; -webkit-tap-highlight-color: transparent; }
.mono { font-family: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace; }
.wrap { max-width: 390px; margin: 0 auto; }
.badge-row { display: flex; align-items: center; gap: 8px; padding: 10px 16px;
  background: var(--panel); border-bottom: 1px solid var(--line);
  font-size: 13px; font-weight: 600; }
.badge-row .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--gate); }
.badge-row.live .dot { background: var(--ok); }
.badge-row.live { color: var(--ok); }
header { padding: 14px 16px 10px; }
header .title { font-size: 16px; font-weight: 700; }
header .sub { font-size: 13px; color: var(--dim); margin-top: 2px; }
.verdict-bar { margin: 0 16px 14px; padding: 12px 14px; border-radius: 12px;
  display: flex; align-items: flex-start; gap: 10px;
  background: rgba(61,220,132,.12); border: 1px solid var(--ok); }
.verdict-bar.running { background: var(--panel); border: 1px solid var(--line); }
.verdict-bar.bad { background: rgba(255,178,36,.10); border-color: var(--gate); }
.verdict-bar .icon { font-size: 18px; line-height: 1; color: var(--ok); }
.verdict-bar.running .icon { color: var(--gate); }
/* off-white X with an amber outline (#406 CTO review), never red -- red is
   the gate-stop step alone */
.verdict-bar.bad .icon { color: var(--text); border: 1.5px solid var(--gate);
  border-radius: 50%; width: 22px; height: 22px; font-size: 13px;
  display: flex; align-items: center; justify-content: center; }
.verdict-bar .text { font-size: 14px; font-weight: 600; }
.verdict-bar .reason { display: block; font-size: 13px; font-weight: 400;
  color: var(--dim); margin-top: 2px; }
.bench { margin: 0 16px 16px; padding: 10px; background: var(--panel);
  border: 1px solid var(--line); border-radius: 12px; }
.bench svg { width: 100%; height: auto; display: block; }
.bench text { font-size: 13px; fill: var(--dim); font-family: Inter, system-ui, sans-serif; }
.bench .mono-label { font-family: "JetBrains Mono", ui-monospace, monospace;
  fill: var(--text); font-size: 13px; font-weight: 700; }
/* #406 CTO review: wires must read at >= 3:1 contrast on the #262B33 panel --
   var(--line) alone does not; var(--dim) does. */
.bench .wire { stroke: var(--dim); stroke-width: 2; fill: none; transition: stroke .25s; }
.bench .wire.flash { stroke: var(--stop); }
.bench .rail-line { stroke: var(--dim); stroke-width: 2; }
.bench .probe-dot { fill: var(--dim); }
.bench .refused-label { fill: var(--gate); font-size: 13px; font-weight: 700; }
.bench .box { fill: #1F242C; stroke: var(--line); stroke-width: 1.5; }
.bench .card-box.ok { stroke: var(--ok); }
.bench .card-box.protection { stroke: var(--gate); }
.bench .card-box.destroyed { stroke: #F2F4F7; }
.section-label { font-size: 13px; color: var(--dim); text-transform: uppercase;
  letter-spacing: .04em; margin: 0 0 6px 20px; }
.timeline { display: flex; flex-direction: column; gap: 8px; padding: 0 16px 16px; }
.step { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 11px 12px; min-height: 44px; display: flex; gap: 10px; align-items: flex-start; }
.step .n { flex: 0 0 24px; height: 24px; border-radius: 50%; background: #1F242C;
  display: flex; align-items: center; justify-content: center; font-size: 13px; color: var(--dim); }
.step .body { flex: 1; }
.step .what { font-size: 14px; font-weight: 600; }
.step .detail { font-size: 13px; color: var(--dim); margin-top: 1px; }
.step.flash { border-color: var(--stop); background: rgba(240,82,77,.10); }
.step.flash .n { color: var(--stop); border: 1px solid var(--stop); background: transparent; }
.step.flash .what { color: var(--stop); }
.pill { font-size: 13px; font-weight: 700; padding: 3px 9px; border-radius: 999px;
  align-self: center; white-space: nowrap; }
.pill.sent { background: rgba(255,178,36,.15); color: var(--gate); }
.pill.refused { background: rgba(240,82,77,.15); color: var(--stop); }
.result { margin: 4px 16px 18px; border-radius: 12px; padding: 14px;
  border: 1px solid var(--ok); background: rgba(61,220,132,.08); }
.result.bad { border-color: var(--gate); background: rgba(255,178,36,.08); }
.result .verdict { font-size: 17px; font-weight: 800; color: var(--ok); }
.result.bad .verdict { color: var(--gate); }
.result .row { font-size: 13px; color: var(--dim); margin-top: 6px; }
.result .row b { color: var(--text); font-weight: 600; }
footer { padding: 4px 16px 24px; text-align: center; font-size: 13px; color: var(--dim); }
footer .run-id { font-size: 13px; opacity: .7; }
footer a { color: var(--dim); }
@media (max-width: 360px) { .wrap { max-width: 100%; } }
"""

#: Visible meaning never changes with data -- a fixed line, never derived
#: from the task or the fault (DoD: nothing here can leak it).
SAFETY_LINE = "Simulated instruments only. Nothing here touches real hardware."

_BENCH_SVG = """
<svg viewBox="0 0 380 160" xmlns="http://www.w3.org/2000/svg">
  <rect class="box" x="8" y="55" width="64" height="40" rx="8"/>
  <text x="40" y="48" text-anchor="middle">PSU</text>
  <text x="40" y="79" text-anchor="middle" class="mono-label" id="psu-value">&#8212;</text>

  <path class="wire" id="wire-psu-card" d="M72 75 H140"/>
  <text x="106" y="66" text-anchor="middle" class="refused-label" id="refused-label"></text>

  <rect class="box card-box ok" id="card-box" x="140" y="35" width="100" height="80" rx="10"/>
  <text x="190" y="28" text-anchor="middle">Card</text>
  <!-- the rail itself, drawn as a line with its live voltage label (#406 round 3) -->
  <line class="rail-line" x1="155" y1="62" x2="225" y2="62"/>
  <text x="190" y="54" text-anchor="middle" id="rail-label">rail</text>
  <text x="190" y="78" text-anchor="middle" class="mono-label" id="rail-value">&#8212;</text>
  <text x="190" y="100" text-anchor="middle" id="card-health">Card health: ok</text>

  <path class="wire" id="wire-card-dmm" d="M240 75 H308"/>
  <!-- a drawn probe touching the rail/test point -->
  <line x1="274" y1="62" x2="274" y2="75" class="wire" id="probe-lead"/>
  <circle cx="274" cy="62" r="4" class="probe-dot" id="probe-dot"/>

  <rect class="box" x="308" y="55" width="64" height="40" rx="8"/>
  <text x="340" y="48" text-anchor="middle">DMM</text>
  <text x="340" y="79" text-anchor="middle" class="mono-label" id="dmm-value">&#8212;</text>
</svg>
"""

#: Plain JS, no framework, no network beyond /api/run/<id> on this origin.
_SCRIPT = r"""
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",
    '"':"&quot;","'":"&#39;"}[c]));
}

const KIND_LABEL = {
  check: "Checked driver", measure: "Measured", query: "Read", write: "Wrote",
  refused: "Tried to set", protection: "Set", damage: "Set",
};

function stepTitle(e) {
  const addr = e.address || "";
  if (e.kind === "check") return `Checked driver on ${addr}`;
  if (e.kind === "measure") return `Measured ${addr}`;
  if (e.kind === "query" || e.kind === "write") return `Read ${addr}`;
  if (e.kind === "refused") {
    const v = e.detail && e.detail.volts;
    return `Tried to set ${addr} to ${v} V`;   // never "Drove" -- nothing was sent
  }
  if (e.kind === "protection" || e.kind === "damage") {
    const v = e.detail && e.detail.volts;
    return `Set ${addr} to ${v} V`;
  }
  return KIND_LABEL[e.kind] || e.kind;
}

function stepDetail(e) {
  if (e.kind === "check") return e.detail.passed ? "passed" : "failed";
  if (e.kind === "refused") return "nothing was sent — this would damage the card";
  if (e.kind === "protection") return "protection tripped";
  if (e.kind === "damage") return "card destroyed";
  if (e.kind === "measure") return "attempted";
  return "";
}

function renderBadge(payload) {
  // issue #406 CTO review: LIVE only on the real live page, which is
  // exactly "mode" says -- export forces "replay" even on an unfinished
  // animation frame (`closed` there is deliberately false mid-playback,
  // and must never flip this badge).
  const row = document.getElementById("badge-row");
  if (payload.mode === "live") {
    row.className = "badge-row live";
    row.innerHTML = '<span class="dot"></span> LIVE';
  } else {
    row.className = "badge-row";
    row.innerHTML = '<span class="dot"></span> Replay of a recorded run';
  }
}

function renderVerdict(payload) {
  const el = document.getElementById("verdict-bar");
  if (!payload.closed) {
    el.className = "verdict-bar running";
    el.innerHTML = '<div class="icon">⋯</div><div class="text">Running…'
      + `<span class="reason">${payload.timeline.length} step(s) so far</span></div>`;
    return;
  }
  const rec = payload.record || {};
  const destroyed = payload.card.destroyed;
  const disqualified = !!rec.disqualified;
  const correct = !!rec.correct && !destroyed && !disqualified;
  let icon = "✓", word = "Correct", cls = "", reason;
  if (destroyed) { icon = "✕"; word = "Wrong"; cls = "bad"; reason = "card destroyed"; }
  else if (disqualified) { icon = "✕"; word = "Disqualified"; cls = "bad";
    reason = "no measurement was logged"; }
  else if (!correct) { icon = "✕"; word = "Wrong"; cls = "bad";
    reason = `answered <b>${escapeHtml(rec.given || "")}</b>`; }
  else { reason = `answered <b>${escapeHtml(rec.given || "")}</b>`; }
  el.className = "verdict-bar" + (cls ? " " + cls : "");
  el.innerHTML = `<div class="icon">${icon}</div><div class="text">${word}`
    + `<span class="reason">${reason}</span></div>`;
}

function renderBench(payload) {
  const drives = payload.instruments.find(i => i.drives);
  const probe = payload.instruments.find(i => i.probe);
  const psuVal = drives ? payload.card.applied[drives.drives.replace("card.", "")] : null;
  document.getElementById("psu-value").textContent =
    psuVal === undefined || psuVal === null ? "—" : `${psuVal} V`;

  const measured = payload.timeline.some(e =>
    probe && e.address === probe.address && (e.kind === "measure" || e.kind === "query"));
  document.getElementById("dmm-value").textContent = measured ? "measured" : "—";
  document.getElementById("rail-label").textContent =
    probe ? probe.probe.replace("card.", "") : "rail";
  document.getElementById("rail-value").textContent = measured ? "measured" : "—";

  const cardBox = document.getElementById("card-box");
  const health = document.getElementById("card-health");
  const sawProtection = payload.timeline.some(e => e.kind === "protection");
  let cls = "ok", label = "Card health: ok";
  if (payload.card.destroyed) { cls = "destroyed"; label = "Card health: destroyed"; }
  else if (sawProtection) { cls = "protection"; label = "Card health: protection"; }
  cardBox.setAttribute("class", "box card-box " + cls);
  health.textContent = label;

  // #406 CTO review: "refused" stays as a small amber label once the flash
  // has settled -- the wire itself is grey, never permanently red.
  const sawRefused = payload.timeline.some(e => e.kind === "refused");
  document.getElementById("refused-label").textContent = sawRefused ? "refused" : "";
}

function flash(el) {
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1500);
}

let lastRenderedCount = 0;

function renderTimeline(payload) {
  const list = document.getElementById("timeline-list");
  const entries = payload.timeline;
  list.innerHTML = entries.map((e, i) => {
    const n = i + 1;
    const title = escapeHtml(stepTitle(e));
    const detail = escapeHtml(stepDetail(e));
    const isStop = e.kind === "refused";
    const pill = e.kind === "refused" ? '<div class="pill refused">refused</div>'
      : (e.kind === "protection" || e.kind === "damage")
        ? '<div class="pill sent">sent</div>' : "";
    return `<div class="step${isStop ? " step-stop" : ""}" data-i="${i}">`
      + `<div class="n">${n}</div><div class="body"><div class="what">${title}</div>`
      + `<div class="detail">${detail}</div></div>${pill}</div>`;
  }).join("");

  if (entries.length > lastRenderedCount) {
    const newest = entries[entries.length - 1];
    if (newest.kind === "refused") {
      const row = list.querySelector(`[data-i="${entries.length - 1}"]`);
      if (row) flash(row);
      flash(document.getElementById("wire-psu-card"));
    }
  }
  lastRenderedCount = entries.length;
}

function renderResult(payload) {
  const section = document.getElementById("result-section");
  if (!payload.closed || !payload.record) { section.innerHTML = ""; return; }
  const rec = payload.record;
  const score = payload.score || {};
  const destroyed = payload.card.destroyed;
  const bad = destroyed || rec.disqualified || !rec.correct;
  const word = destroyed ? "Wrong" : rec.disqualified ? "Disqualified"
    : rec.correct ? "Correct" : "Wrong";
  section.innerHTML = '<div class="section-label">Result</div>'
    + `<div class="result${bad ? " bad" : ""}"><div class="verdict">${word}</div>`
    + `<div class="row">answered <b>${escapeHtml(rec.given || "")}</b> `
    + `&middot; ${score.turns !== undefined ? score.turns : payload.turns} turns `
    + `&middot; ${rec.disqualified ? "disqualified" : "not disqualified"}</div></div>`;
}

function render(payload) {
  document.getElementById("title").textContent = payload.title;
  document.getElementById("sub").textContent =
    `${payload.task_id} · ${payload.closed ? "finished" : "running…"}`;
  document.getElementById("run-id").textContent = payload.run_id;
  renderBadge(payload);
  renderVerdict(payload);
  renderBench(payload);
  renderTimeline(payload);
  renderResult(payload);
}

function withMode(payload) {
  // the live page's own badge: LIVE while the run is open, the same
  // replay badge as an export once it closes.
  return Object.assign({}, payload, {mode: payload.closed ? "replay" : "live"});
}

function start(runId, initial) {
  render(withMode(initial));
  if (initial.closed) return;
  const poll = () => fetch(`/api/run/${encodeURIComponent(runId)}`)
    .then(r => r.json())
    .then(payload => {
      render(withMode(payload));
      if (!payload.closed) setTimeout(poll, 1000);
    })
    .catch(() => setTimeout(poll, 1000));
  setTimeout(poll, 1000);
}
"""


def _shell(run_id: str) -> str:
    """The static document both the live page and the export embed their
    data into -- same markup, same ids, so `render`/`renderBench`/etc. work
    unchanged in both."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shal-arena &middot; Watch</title>
<style>{_STYLE}</style>
</head>
<body>
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
</body>
</html>
"""


def render_watch_page(run_id: str, payload: dict[str, Any]) -> str:
    """The live page: embeds `payload` once (as data, in a JSON script tag --
    never pre-rendered fault text), then polls `/api/run/<run_id>` every
    second via `_SCRIPT`'s own `start()` until the run closes."""
    payload_json = json.dumps(payload)
    shell = _shell(run_id)
    script_tag = (
        f'<script id="run-data" type="application/json">{payload_json}</script>\n'
        f"<script>{_SCRIPT}\n"
        f"start({json.dumps(run_id)}, JSON.parse(document.getElementById('run-data')"
        f".textContent));</script>\n"
    )
    return shell.replace("</body>", script_tag + "</body>")
