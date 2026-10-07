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

import html
import json
from typing import Any

REPO_URL = "https://github.com/determlab/shal"


def safe_json(obj: Any) -> str:
    """`json.dumps`, with every `<` escaped (issue #406 CTO review: a
    `</script>` inside the DATA -- the agent's own answer, never checked
    against the fault ids, can legitimately be any string -- would close
    the script tag it sits in and inject markup). JSON strings never need a
    literal `<`, so this is lossless; the embedding page always reads it
    back with `JSON.parse`, never as literal HTML."""
    return json.dumps(obj).replace("<", "\\u003c")

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
header .question { font-size: 13px; color: var(--text); margin-top: 6px; }
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
/* #406 CTO review round 4: the SVG's own font-size is in viewBox units,
   not CSS px -- a 380-wide viewBox renders ~338px wide inside the 390px
   .wrap (16px side margins + 10px padding each side). 15 units *
   (338/380) ~= 13.3 actual px -- the boxes were widened (not just the
   font bumped) so labels like "protection" and "2.9 V" still fit. */
.bench text { font-size: 15px; fill: var(--dim); font-family: Inter, system-ui, sans-serif; }
.bench .mono-label { font-family: "JetBrains Mono", ui-monospace, monospace;
  fill: var(--text); font-size: 15px; font-weight: 700; }
/* #406 CTO review: wires must read at >= 3:1 contrast on the #262B33 panel --
   var(--line) alone does not; var(--dim) does. */
.bench .wire { stroke: var(--dim); stroke-width: 2; fill: none; transition: stroke .25s; }
.bench .wire.flash { stroke: var(--stop); }
.bench .rail-line { stroke: var(--dim); stroke-width: 2; }
.bench .probe-dot { fill: var(--dim); }
.bench .refused-dot { fill: var(--gate); }
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

#: issue #406 CTO review round 4: wider boxes (not just a bigger font) so
#: every label clears its own box at a real 390 px width -- "protection"
#: and "2.9 V" both have to fit the DMM/card boxes, not just the font
#: floor. "Card health:" is dropped; the word alone ("ok"/"protection"/
#: "destroyed") is what the border colour already names.
_BENCH_SVG = """
<svg viewBox="0 0 380 170" xmlns="http://www.w3.org/2000/svg">
  <rect class="box" x="6" y="58" width="64" height="42" rx="8"/>
  <text x="38" y="50" text-anchor="middle">PSU</text>
  <text x="38" y="83" text-anchor="middle" class="mono-label" id="psu-value">&#8212;</text>

  <path class="wire" id="wire-psu-card" d="M70 79 H118"/>
  <!-- #427: a dot, not text -- "refused" as a word did not clear the 48-unit
       gap between the PSU and card boxes at any font that still reads as
       body text. The timeline's own "refused" pill already carries the
       word; this is just a persistent, silent reminder on the wire. -->
  <circle cx="94" cy="79" r="4" class="refused-dot" id="refused-dot" style="display:none"/>

  <rect class="box card-box ok" id="card-box" x="118" y="32" width="144" height="92" rx="10"/>
  <text x="190" y="24" text-anchor="middle">Card</text>
  <!-- the rail itself, drawn as a line with its live voltage label (#406 round 3) -->
  <line class="rail-line" x1="138" y1="58" x2="242" y2="58"/>
  <text x="190" y="50" text-anchor="middle" id="rail-label">rail</text>
  <text x="190" y="76" text-anchor="middle" class="mono-label" id="rail-value">&#8212;</text>
  <text x="190" y="100" text-anchor="middle" id="card-health">ok</text>

  <path class="wire" id="wire-card-dmm" d="M262 79 H300"/>
  <!-- the probe sits ON the test point (the rail line's own end, inside the
       card box), #406 CTO review round 3 -- not floating on the wire. -->
  <line x1="242" y1="58" x2="262" y2="79" class="wire" id="probe-lead"/>
  <circle cx="242" cy="58" r="4" class="probe-dot" id="probe-dot"/>

  <rect class="box" x="300" y="58" width="74" height="42" rx="8"/>
  <text x="337" y="50" text-anchor="middle">DMM</text>
  <text x="337" y="83" text-anchor="middle" class="mono-label" id="dmm-value">&#8212;</text>

  <!-- issue #427 CTO review: the relay-rail task's own relay0/temp0 -- a
       second row, hidden (and the viewBox left at its 2-instrument size)
       unless the run actually has 4 instruments, so a 2-instrument run's
       layout is unchanged. -->
  <g id="extra-instruments" style="display:none">
    <rect class="box" x="6" y="128" width="64" height="36" rx="8"/>
    <text x="38" y="122" text-anchor="middle">RELAY</text>
    <text x="38" y="151" text-anchor="middle" class="mono-label" id="relay-value">&#8212;</text>
    <!-- #427 CTO review round 2: the wire must reach the card box itself
         (its bottom-left corner, 118,124), not end in empty space below it. -->
    <path class="wire" d="M70 146 H118 V124"/>

    <rect class="box" x="300" y="128" width="74" height="36" rx="8"/>
    <text x="337" y="122" text-anchor="middle">TEMP</text>
    <text x="337" y="151" text-anchor="middle" class="mono-label" id="temp-value">&#8212;</text>
    <path class="wire" d="M262 124 V146 H300"/>
  </g>
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
  refused: "Tried to set", protection: "Set", damage: "Set", reading: "Measured",
};

// issue #427 CTO review: a number is shown with a unit symbol everywhere
// it appears -- bench boxes, timeline rows and the closing sentence alike
// (fixes "90.00114442664224°C" running off its box, and "3.3 volt"
// touching the box edge). Round 3: temperatures are 1 decimal ("90.0 °C"),
// voltages stay 2 ("3.30 V") -- a temperature reading is never as precise
// as a calibrated voltage reference.
function fmtNum(value, unit) {
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (unit === "celsius") return `${n.toFixed(1)} °C`;
  return `${n.toFixed(2)} V`;
}

function stepTitle(e) {
  const addr = e.address || "";
  if (e.kind === "check") return `Checked driver on ${addr}`;
  if (e.kind === "reading" || e.kind === "measure") return `Measured ${addr}`;
  if (e.kind === "query") return `Read ${addr}`;
  if (e.kind === "write") {
    // issue #427 CTO review round 3: a clean, in-range drive is now its
    // own step. issue #457: `drive` goes through SHAL's own gate, not the
    // agent's driver op -- the row says so, never `set_voltage`. issue
    // #481: the row says what happened to the card, in plain words.
    const v = e.detail && e.detail.volts;
    const powered = `Powered the card at ${fmtNum(v)}, through the SHAL gate`;
    return v === undefined ? `Wrote ${addr}` : powered;
  }
  if (e.kind === "call") {
    const op = e.detail && e.detail.op, args = (e.detail && e.detail.args) || [];
    if (op === "set_relay") return `Switched ${addr} ${args[1] === "true" ? "on" : "off"}`;
    if (op === "read_relay") return `Read ${addr}`;
    return `Called ${op} on ${addr}`;
  }
  if (e.kind === "exchange") {
    // issue #457: the real bus-layer exchange, never invented -- Modbus's
    // own structured message (addr/value or addr/bits), or raw bytes
    // already hex-encoded (I2C, via `shal.log.redact`).
    const fam = e.detail && e.detail.bus_family, req = e.detail && e.detail.request;
    if (fam === "sim_msg" && req) {
      if (req.fc === 5) return `Switched ${addr} ${req.value ? "on" : "off"}`;
      if (req.fc === 1) return `Read ${addr}`;
    }
    return `Exchanged with ${addr}`;
  }
  if (e.kind === "refused") {
    const v = e.detail && e.detail.volts;
    return `Tried to set ${addr} to ${fmtNum(v)}`;   // never "Drove" -- nothing was sent
  }
  if (e.kind === "protection" || e.kind === "damage") {
    const v = e.detail && e.detail.volts;
    return v === undefined ? `Protection tripped on ${addr}` : `Set ${addr} to ${fmtNum(v)}`;
  }
  return KIND_LABEL[e.kind] || e.kind;
}

function stepDetail(e) {
  if (e.kind === "check") return e.detail.passed ? "passed" : "failed";
  if (e.kind === "call") return e.detail.ok ? "ok" : "failed";
  if (e.kind === "refused") return "nothing was sent — this would damage the card";
  if (e.kind === "protection") return "protection tripped";
  if (e.kind === "damage") return "card destroyed";
  if (e.kind === "reading") return fmtNum(e.detail.value, e.detail.unit);
  if (e.kind === "measure") return "attempted";
  // a SCPI query/write row (issue #457 round 2 nit): show the command and
  // its real answer, the same way an `exchange` row already does -- a
  // bare "Read dmm0" with no detail at all left out the cmd/reply the
  // data has carried since #457.
  if ((e.kind === "query" || e.kind === "write") && e.detail.cmd !== undefined) {
    const reply = e.detail.reply;
    return reply === undefined || reply === "" ? e.detail.cmd : `${e.detail.cmd} -> ${reply}`;
  }
  if (e.kind === "exchange") {
    const req = JSON.stringify(e.detail.request), res = JSON.stringify(e.detail.response);
    return res === undefined || res === '""' ? req : `${req} -> ${res}`;
  }
  return "";
}

// issue #474 (regression from #459): `kind: "exchange"` rows are the raw
// bus-layer log #459 added to `payload.timeline` -- real data, kept there
// for #447's own log table, but never a timeline STEP: each one merely
// restates, in raw protocol text, what an agent step (call/write/measure)
// already shows. Every reader of `payload.timeline` for display -- the
// step list and the "N step(s) so far" count alike -- goes through this
// first; nothing in `payload.timeline` itself, or its length, changes.
function visibleSteps(entries) {
  return entries.filter(e => e.kind !== "exchange");
}

// a bare "measure" marker paired with a later "reading" at the same
// address is one real step, told by the reading's own row -- not two.
function hasPairedReading(entries, i) {
  // "measure" (the neutral marker) and "query" (the raw bus exchange) are
  // both the SAME real read a "reading" entry already names with its
  // actual value -- one row, not three.
  const e = entries[i];
  if (e.kind !== "measure" && e.kind !== "query") return false;
  return entries.some(o => o.kind === "reading" && o.address === e.address);
}

// issue #481: the rows the timeline really shows -- `visibleSteps` plus the
// `hasPairedReading` fold -- in one place. `renderTimeline`, the "N
// step(s) so far" count and the export's `startExport` all read this, so
// the count always matches the rows and a hidden row never costs a replay
// tick. Applying it twice gives the same rows (an unpaired measure has no
// reading to fold into, in any subset either).
function shownSteps(entries) {
  const visible = visibleSteps(entries);
  return visible.filter((e, i) => !hasPairedReading(visible, i));
}

function latestReading(entries, address) {
  for (let i = entries.length - 1; i >= 0; i--) {
    if (entries[i].kind === "reading" && entries[i].address === address) return entries[i];
  }
  return null;
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
      + `<span class="reason">${shownSteps(payload.timeline).length} step(s) so far`
      + "</span></div>";
    return;
  }
  const rec = payload.record || {};
  const destroyed = payload.card.destroyed;
  const disqualified = !!rec.disqualified;
  const correct = !!rec.correct && !destroyed && !disqualified;
  // issue #427 CTO review round 2: this bar and the Result box at the
  // bottom were saying the same thing twice. issue #481: the answer
  // sentence (built server side) now lives here, at the top, and only
  // here -- the Result box no longer repeats it. With no sentence, the
  // bar keeps its one short reason.
  let icon = "✓", word = "Correct", cls = "", reason = "";
  if (destroyed) { icon = "✕"; word = "Wrong"; cls = "bad"; reason = "card destroyed"; }
  else if (disqualified) { icon = "✕"; word = "Disqualified"; cls = "bad";
    reason = "no measurement was logged"; }
  else if (!correct) { icon = "✕"; word = "Wrong"; cls = "bad"; }
  if (payload.answer_sentence) reason = payload.answer_sentence;
  el.className = "verdict-bar" + (cls ? " " + cls : "");
  el.innerHTML = `<div class="icon">${icon}</div><div class="text">${word}`
    + (reason ? `<span class="reason">${escapeHtml(reason)}</span>` : "") + "</div>";
}

// issue #406 body: "<level> level, N instruments, datasheet written by us"
// -- plain text, always visible; the 4-instrument bench caption is the one
// case with its own wording (CTO 2026-10-07), else the two-instrument one,
// else a "coming" placeholder for anything else.
//
// issue #432 CMO wording: "Bench" (the section label) stays short and
// uppercase -- the caption-3 sentence, which varies by instrument count,
// is its own normal plain paragraph underneath, never the label itself.
function renderPlainLine(payload) {
  const n = payload.instruments.length;
  document.getElementById("plain-line").textContent =
    `${payload.level} level, ${n} instrument${n === 1 ? "" : "s"}, datasheet written by us`;
  const caption3 = document.getElementById("bench-caption3");
  if (n === 4) {
    caption3.textContent = "The bench: power supply, multimeter, relay and temperature "
      + "sensor, around one card. Four instruments, three protocols.";
  } else if (n === 2) {
    caption3.textContent = "The bench: power supply, multimeter, card.";
  } else {
    caption3.textContent = "A four-instrument run is on its way.";
  }
  // issue #427 CTO review round 2: caption 4 ("It powers the card and
  // measures the 3.3 V rail.") is the 2-instrument run's own text -- a
  // 4-instrument run says what IT checks instead.
  const caption4 = document.getElementById("bench-caption");
  caption4.textContent = n === 4
    ? "It switches the card on through the relay, then measures the 3.3 V "
      + "rail and the regulator temperature."
    : "It powers the card and measures the 3.3 V rail.";
}

function renderBench(payload) {
  const drives = payload.instruments.find(i => i.drives);
  const probe = payload.instruments.find(i => i.probe);
  const psuVal = drives ? payload.card.applied[drives.drives.replace("card.", "")] : null;
  document.getElementById("psu-value").textContent =
    psuVal === undefined || psuVal === null ? "—" : fmtNum(psuVal);

  const reading = probe ? latestReading(payload.timeline, probe.address) : null;
  const attempted = payload.timeline.some(e =>
    probe && e.address === probe.address && (e.kind === "measure" || e.kind === "query"));
  const valueText = reading ? fmtNum(reading.detail.value, reading.detail.unit)
    : (attempted ? "measured" : "—");
  document.getElementById("dmm-value").textContent = valueText;
  // issue #427 CTO review round 2: the rail's own human label ("3V3
  // rail"), not the bare test-point name ("tp_3v3") a driver addresses it
  // by.
  const probeTestPoint = probe ? probe.probe.replace("card.", "") : null;
  const rail = probeTestPoint
    ? (payload.rails || []).find(r => r.test_point === probeTestPoint) : null;
  document.getElementById("rail-label").textContent = rail ? `${rail.name} rail` : "rail";
  document.getElementById("rail-value").textContent = valueText;

  const cardBox = document.getElementById("card-box");
  const health = document.getElementById("card-health");
  const sawProtection = payload.timeline.some(e => e.kind === "protection");
  // #406 CTO review round 4: the word alone -- "Card health:" did not fit
  // the box even at the wider size, and the border colour already says
  // which this is.
  let cls = "ok", label = "ok";
  if (payload.card.destroyed) { cls = "destroyed"; label = "destroyed"; }
  else if (sawProtection) { cls = "protection"; label = "protection"; }
  cardBox.setAttribute("class", "box card-box " + cls);
  health.textContent = label;

  // #406 CTO review: "refused" stays as a small amber label once the flash
  // has settled -- the wire itself is grey, never permanently red.
  const sawRefused = payload.timeline.some(e => e.kind === "refused");
  document.getElementById("refused-dot").style.display = sawRefused ? "" : "none";

  // issue #427 CTO review: the relay-rail task's second row (relay0,
  // temp0) -- shown only for an actual 4-instrument run, so a
  // 2-instrument run's bench is pixel-identical to before.
  const extra = document.getElementById("extra-instruments");
  const svg = document.querySelector(".bench svg");
  const n = payload.instruments.length;
  extra.style.display = n === 4 ? "" : "none";
  if (svg) svg.setAttribute("viewBox", n === 4 ? "0 0 380 210" : "0 0 380 170");
  if (n === 4) {
    document.getElementById("relay-value").textContent =
      payload.card.power_on === false ? "off" : "on";
    const temp = payload.instruments.find(i => i.address.startsWith("temp"));
    const tReading = temp ? latestReading(payload.timeline, temp.address) : null;
    document.getElementById("temp-value").textContent = tReading
      ? fmtNum(tReading.detail.value, "celsius") : "—";
  }
}

function flash(el) {
  el.classList.add("flash");
  setTimeout(() => el.classList.remove("flash"), 1500);
}

let lastRenderedCount = 0;

function stepRowHtml(e, i) {
  const n = i + 1;
  const title = escapeHtml(stepTitle(e));
  const detail = escapeHtml(stepDetail(e));
  const pill = e.kind === "refused" ? '<div class="pill refused">refused</div>'
    : (e.kind === "protection" || e.kind === "damage")
      ? '<div class="pill sent">sent</div>' : "";
  return `<div class="step" data-i="${i}">`
    + `<div class="n">${n}</div><div class="body"><div class="what">${title}</div>`
    + `<div class="detail">${detail}</div></div>${pill}</div>`;
}

// issue #427 CTO review round 2: the scripted-checks section is SHAL's own
// fixed, always-present text about what the gate and the error/fail split
// do in general -- it is never built from this run's own timeline, and an
// agent's own refused call (e.g. a real 30 V attempt it made) is never
// moved out of its own timeline into this section or relabeled as one of
// these two. Everything the agent actually did stays in the timeline, in
// its own order, in full.
// issue #457 (scope added): the page shows each instrument's role as
// plain words BUILT from the task yaml's own `drives:`/`probe:`/`switches:` field and
// the rail/temp-point data already in the payload -- "powers VIN",
// "measures the 3V3 rail", "measures the regulator temperature" -- never
// hand-written text. The agent-facing JSON (`payload.instruments[].role`,
// `drives`/`probe` themselves) is untouched; this is display only, and no
// diagram change (the card, visual and layout stay with #447).
function instrumentRoleText(payload, inst) {
  if (inst.drives) {
    const name = inst.drives.split(".")[1] || inst.drives;
    return `powers ${name.toUpperCase()}`;
  }
  if (inst.switches) {   // issue #473: a relay only turns the input on/off
    const name = inst.switches.split(".")[1] || inst.switches;
    return `switches ${name.toUpperCase()} on/off`;
  }
  if (inst.probe) {
    const tp = inst.probe.split(".")[1] || inst.probe;
    const rail = (payload.rails || []).find(r => r.test_point === tp);
    if (rail) return `measures the ${rail.name} rail`;
    const temp = (payload.temp_points || []).find(t => t.test_point === tp);
    if (temp) return `measures the ${temp.name} temperature`;
  }
  return inst.role;   // fallback: the raw server-built text, never blank
}

function renderRoles(payload) {
  const section = document.getElementById("roles-section");
  const rows = payload.instruments.map(i =>
    `<p class="plain-line" style="margin-left:0">`
    + `<span class="mono">${escapeHtml(i.address)}</span> `
    + `${escapeHtml(instrumentRoleText(payload, i))}</p>`
  ).join("");
  section.innerHTML = rows;
}

function renderTimeline(payload) {
  const list = document.getElementById("timeline-list");
  const agentSteps = shownSteps(payload.timeline);
  list.innerHTML = agentSteps.map((e, i) => stepRowHtml(e, i)).join("");

  if (agentSteps.length > lastRenderedCount) {
    const newest = agentSteps[agentSteps.length - 1];
    if (newest.kind === "refused") flash(document.getElementById("wire-psu-card"));
  }
  lastRenderedCount = agentSteps.length;
}

// issue #444: the scripted checks did not happen in THIS replayed run
// (gate_stops: 0) -- the heading and both lines say so explicitly, exact
// CMO text, so a reader never mistakes them for part of the agent's own
// replay above.
function renderScriptedSection(payload) {
  const section = document.getElementById("scripted-section");
  section.innerHTML = '<div class="end-section"><div class="section-label">'
    + "Not in this run: two checks from the scripted demo</div>"
    + '<p class="plain-line" style="margin-left:0">'
    + "In the scripted demo (<code>shal-arena demo</code>), a step asks for 30 V "
    + "on purpose. The gate stops it before anything is sent.</p>"
    + '<p class="plain-line" style="margin-left:0">'
    + "In the scripted demo, a cable is unplugged. The result is error, not fail: "
    + "the card is not blamed.</p>"
    + "</div>";
}

// issue #481: the answer sentence (built server side, `_answer_sentence`
// in the data module) is shown once, in the verdict bar at the top
// (`renderVerdict`, through `escapeHtml`) -- this box keeps its other rows
// and no longer repeats it.
function renderResult(payload) {
  const section = document.getElementById("result-section");
  if (!payload.closed || !payload.record) { section.innerHTML = ""; return; }
  const rec = payload.record;
  const score = payload.score || {};
  const destroyed = payload.card.destroyed;
  const bad = destroyed || rec.disqualified || !rec.correct;
  const word = destroyed ? "Wrong" : rec.disqualified ? "Disqualified"
    : rec.correct ? "Correct" : "Wrong";
  // issue #406 body, caption 7 -- fixed CMO text, always above the result.
  section.innerHTML = '<div class="section-label">Result</div>'
    + '<p class="plain-line">The answer: which measurement failed, against which limit.</p>'
    + `<div class="result${bad ? " bad" : ""}"><div class="verdict">${word}</div>`
    + `<div class="row">answered <b>${escapeHtml(rec.given || "")}</b> `
    + `&middot; ${score.turns !== undefined ? score.turns : payload.turns} turns `
    + `&middot; ${rec.disqualified ? "disqualified" : "not disqualified"}</div></div>`;
}

// issue #406 follow-up: "Drivers written by the agent" -- folded, one line
// above each: "The driver the agent wrote for the <name>, <n> lines".
// Built with textContent, never innerHTML, so the code itself (arbitrary
// text) can never be interpreted as markup. Captions 1 and 2 (fixed CMO
// text) sit above the fold: the agent reads the datasheet, then writes and
// checks the driver -- this step always happened, so the two captions show
// with or without --driver (issue #427 CTO review round 2); only the
// folded code blocks below them are conditional on `drivers` being given.
function renderDriverCode(payload) {
  const section = document.getElementById("driver-code-section");
  const drivers = payload.drivers || {};
  const names = Object.keys(drivers);
  section.innerHTML = "";
  const label = document.createElement("div");
  label.className = "section-label";
  label.textContent = "Drivers written by the agent";
  section.appendChild(label);
  const caption1 = document.createElement("p");
  caption1.className = "plain-line";
  caption1.textContent = "The agent reads each instrument's datasheet.";
  section.appendChild(caption1);
  const caption2 = document.createElement("p");
  caption2.className = "plain-line";
  caption2.textContent = "It writes a driver for each one and checks it.";
  section.appendChild(caption2);
  for (const name of names) {
    const d = drivers[name];
    const details = document.createElement("details");
    details.className = "driver-code";
    const summary = document.createElement("summary");
    summary.textContent = `The driver the agent wrote for the ${name}, ${d.lines} lines`;
    const pre = document.createElement("pre");
    pre.textContent = d.code;
    details.appendChild(summary);
    details.appendChild(pre);
    section.appendChild(details);
  }
}

function render(payload) {
  document.getElementById("title").textContent = payload.title;
  document.getElementById("sub").textContent =
    `${payload.task_id} · ${payload.closed ? "finished" : "running…"}`;
  // issue #474: the task's own question, under the title -- textContent
  // only, same as every other agent-written or task-written field on this
  // page.
  document.getElementById("question").textContent = payload.question || "";
  document.getElementById("run-id").textContent = payload.run_id;
  renderBadge(payload);
  renderVerdict(payload);
  renderPlainLine(payload);
  renderBench(payload);
  renderDriverCode(payload);
  renderRoles(payload);
  renderTimeline(payload);
  renderScriptedSection(payload);
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


#: issue #406 body (CMO text, source ops/projects/shal/game-proposal.md
#: s15) -- the problem this page solves, verbatim, above the bench, on
#: both the live page and the export. Never the fault, never a number from
#: a benchmark run.
PROBLEM_LINES = (
    "A good unit fails on the line. The line stops. Two hours later it "
    "turns out to be a cable.",
    "An agent driving real instruments can destroy a card with one wrong "
    "command, like 30 V on a 5 V rail.",
    "SHAL tells a cable fault (error) from a bad unit (fail), and stops a "
    "dangerous command before it is sent.",
)


def _banner_html(extra: str) -> str:
    return f'<div class="export-label">{extra}</div>' if extra else ""


def _shell(run_id: str, *, banner: str = "") -> str:
    """The static document both the live page and the export embed their
    data into -- same markup, same ids, so `render`/`renderBench`/etc. work
    unchanged in both. `banner` is the export's own replay label (empty on
    the live page, which has no equivalent static line -- `renderBadge`
    covers it)."""
    problem_html = "".join(f"<p>{html.escape(line)}</p>" for line in PROBLEM_LINES)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shal-arena &middot; Watch</title>
<style>{_STYLE}
.export-label {{ background: var(--panel); color: var(--text); font-size: 13px;
  font-weight: 700; text-align: center; padding: 10px 16px; border-bottom: 1px solid var(--line); }}
.problem {{ margin: 0 16px 14px; padding: 12px 14px; border-radius: 12px;
  background: var(--panel); border: 1px solid var(--line); }}
.problem p {{ margin: 0 0 6px; font-size: 14px; }}
.problem p:last-child {{ margin-bottom: 0; }}
.end-section {{ margin: 20px 16px 0; padding-top: 14px; border-top: 1px solid var(--line); }}
.end-section .section-label {{ margin-left: 0; }}
.driver-code {{ margin: 0 16px 14px; }}
.driver-code summary {{ font-size: 14px; cursor: pointer; padding: 10px 12px;
  background: var(--panel); border: 1px solid var(--line); border-radius: 10px; }}
.driver-code pre {{ font-size: 13px; line-height: 1.4; background: #15181D;
  border: 1px solid var(--line); border-top: none; border-radius: 0 0 10px 10px;
  padding: 10px 12px; overflow-x: auto; margin: 0; }}
.plain-line {{ margin: 0 16px 10px; font-size: 13px; color: var(--dim); }}
</style>
</head>
<body>
{_banner_html(banner)}
<div class="wrap">
  <div class="badge-row" id="badge-row"><span class="dot"></span> Replay of a recorded run</div>
  <header>
    <div class="title" id="title"></div>
    <div class="sub" id="sub"></div>
    <div class="question" id="question"></div>
  </header>
  <div class="problem">{problem_html}</div>
  <div class="verdict-bar" id="verdict-bar"></div>
  <div id="plain-line" class="plain-line"></div>
  <div class="section-label" id="bench-label">Bench</div>
  <p class="plain-line" id="bench-caption3"></p>
  <p class="plain-line" id="bench-caption"></p>
  <div class="bench">{_BENCH_SVG}</div>
  <div id="driver-code-section"></div>
  <div id="roles-section"></div>
  <div class="section-label">Timeline</div>
  <div class="timeline" id="timeline-list"></div>
  <div id="scripted-section"></div>
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
    payload_json = safe_json(payload)
    shell = _shell(run_id)
    script_tag = (
        f'<script id="run-data" type="application/json">{payload_json}</script>\n'
        f"<script>{_SCRIPT}\n"
        f"start({safe_json(run_id)}, JSON.parse(document.getElementById('run-data')"
        f".textContent));</script>\n"
    )
    return shell.replace("</body>", script_tag + "</body>")
