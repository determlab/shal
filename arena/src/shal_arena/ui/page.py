"""The arena demo page (issue #406, laid out per the approved #447 layout):
one static document -- the result first (the measured line, the 4-step
strip, and one live visual of the agent, its instruments and the card),
then the card's block diagram (drawn from the card yaml's own `blocks:`),
one card per instrument with what the agent used and what it returned, and
the log of the run. The rendering logic (`_SCRIPT`) is plain JavaScript,
inline, no network beyond `/api/run/<id>` on the same origin -- same rule
the result card already holds (`replay/card.py`): the only outside
reference anywhere is the repo link.

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


def safe_json(obj: Any) -> str:
    """`json.dumps`, with every `<` escaped (issue #406 CTO review: a
    `</script>` inside the DATA -- the agent's own answer, never checked
    against the fault ids, can legitimately be any string -- would close
    the script tag it sits in and inject markup). JSON strings never need a
    literal `<`, so this is lossless; the embedding page always reads it
    back with `JSON.parse`, never as literal HTML."""
    return json.dumps(obj).replace("<", "\\u003c")

#: CTO brand tokens (#406 round 2 approval) -- local stacks only, no network.
#: issue #447: one column on a phone, the card beside the instruments on a
#: desktop. Every SVG's text is sized in viewBox units: 20 units in a
#: 480-wide viewBox is 13.7 px at a 390 px phone (`.page` 16 px gutters,
#: `.box` 14 px padding and 1 px border), the 13 px floor.
_STYLE = """
:root {
  --bg: #1A1D23; --panel: #262B33; --sunk: #1F242C; --line: #3A414D;
  --text: #F2F4F7; --dim: #9BA3AF; --gate: #FFB224; --ok: #3DDC84; --bad: #F0524D;
  --sans: Inter, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  --mono: "JetBrains Mono", ui-monospace, SFMono-Regular, Consolas, monospace;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) { :root:not([data-theme="dark"]) {
  --bg: #F3F4F1; --panel: #FFFFFF; --sunk: #ECEEEA; --line: #CDD2CB;
  --text: #15181D; --dim: #5B6370; --gate: #B86E00; --ok: #17803F; --bad: #C2312C;
  color-scheme: light; } }
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--text);
  font-family: var(--sans); font-size: 15px; line-height: 1.45;
  -webkit-tap-highlight-color: transparent; }
.mono { font-family: var(--mono); }
.page { max-width: 1040px; margin: 0 auto; padding: 16px 16px 40px; display: grid; gap: 16px; }
.labels { font: 600 13px var(--mono); color: var(--gate); letter-spacing: .04em;
  text-transform: uppercase; }
.labels.live { color: var(--ok); }
h1 { font-size: 22px; line-height: 1.25; margin: 4px 0 0; text-wrap: balance; }
.lede { color: var(--dim); margin: 6px 0 0; max-width: 62ch; }
.hero { display: grid; gap: 12px; }
.verdict-bar { display: flex; gap: 8px 10px; align-items: center; flex-wrap: wrap;
  background: var(--panel); border: 1px solid var(--line); border-left: 4px solid var(--ok);
  border-radius: 10px; padding: 14px 16px; }
.verdict-bar.bad { border-left-color: var(--gate); }
.verdict-bar.running { border-left-color: var(--line); }
.verdict-bar .big { flex: 1 1 100%; font-size: 18px; font-weight: 700; }
.verdict-bar .big b { color: var(--bad); }
.verdict-bar .reason { color: var(--dim); font-size: 14px; }
.chip { font: 700 13px var(--mono); padding: 4px 10px; border-radius: 999px; white-space: nowrap; }
.chip.ok { background: color-mix(in srgb, var(--ok) 18%, transparent); color: var(--ok); }
.chip.bad { background: color-mix(in srgb, var(--gate) 18%, transparent); color: var(--gate); }
.chip.dim { background: var(--sunk); color: var(--dim); }
.flow { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
@media (max-width: 560px) { .flow { grid-template-columns: repeat(2, 1fr); } }
.st { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 12px 10px; text-align: center; transition: border-color .3s, background .3s; }
.st svg { width: 44px; height: 44px; display: block; margin: 0 auto 6px; }
.st svg * { stroke: var(--dim); fill: none; stroke-width: 2; stroke-linecap: round;
  stroke-linejoin: round; }
.st .k { font-weight: 700; font-size: 15px; }
.st .s { font-size: 13px; color: var(--dim); font-family: var(--mono); margin-top: 2px; }
.st.lit { border-color: var(--gate);
  background: color-mix(in srgb, var(--gate) 10%, var(--panel)); }
.st.lit svg * { stroke: var(--gate); }
.st.done svg * { stroke: var(--ok); }
.st.fault { border-color: var(--bad);
  background: color-mix(in srgb, var(--bad) 10%, var(--panel)); }
.st.fault svg * { stroke: var(--bad); }
.st.fault .k { color: var(--bad); }
.box { background: var(--panel); border: 1px solid var(--line); border-radius: 10px;
  padding: 14px; min-width: 0; }
.box h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .08em; color: var(--dim);
  margin: 0 0 10px; font-weight: 700; }
.hero-visual { display: grid; gap: 8px; align-items: center; }
@media (min-width: 860px) { .hero-visual { grid-template-columns: minmax(0, 1fr) 240px; } }
.hero-result { font-size: 34px; font-weight: 800; text-align: center; color: var(--dim); }
.hero-result.ok { color: var(--ok); }
.hero-result.bad { color: var(--gate); }
.viz svg { width: 100%; height: auto; display: block; }
.hero-visual .viz svg { max-width: 600px; margin: 0 auto; }
.viz text { font-family: var(--sans); font-size: 20px; fill: var(--text); }
.viz .tp-label { font-family: var(--mono); font-size: 20px; fill: var(--dim); }
.viz .blk { fill: var(--sunk); stroke: var(--line); stroke-width: 2; }
.viz .inst-box { fill: var(--panel); stroke: var(--gate); stroke-width: 2; }
.viz .agent-box { fill: var(--sunk); stroke: var(--text); stroke-width: 2; }
.viz .net { stroke: var(--dim); stroke-width: 2.5; fill: none; }
.viz .probe { stroke: var(--gate); stroke-width: 2; stroke-dasharray: 5 4; fill: none; }
.viz .tp { fill: var(--gate); }
.viz .hot { stroke: var(--bad); stroke-width: 3; }
.viz .on { stroke: var(--ok); }
.viz .active { filter: drop-shadow(0 0 6px var(--gate)); }
.viz .outline { fill: none; stroke: var(--line); stroke-dasharray: 6 4; }
.viz .arrow { stroke: var(--line); stroke-width: 2.5; fill: none; }
.viz .arrow.lit { stroke: var(--gate); }
.viz .arrow.bad { stroke: var(--bad); }
.seq { animation: seqlit .6s ease-out both; animation-delay: calc(var(--i, 0) * .5s); }
@keyframes seqlit { from { opacity: .2; } }
.card-spec { font-size: 13px; color: var(--dim); margin: 0 0 8px; }
.controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
button { font: 600 14px var(--sans); min-height: 44px; padding: 9px 16px; border-radius: 8px;
  border: 1px solid var(--gate); background: var(--gate); color: var(--bg); cursor: pointer; }
button:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
.grid { display: grid; gap: 16px; grid-template-columns: 1fr; }
@media (min-width: 860px) { .grid { grid-template-columns: 1.15fr 1fr; } }
.inst-list { display: grid; gap: 10px; }
.inst { border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  background: var(--sunk); transition: border-color .2s; }
.inst.active { border-color: var(--gate); }
.inst-head { display: flex; justify-content: space-between; gap: 8px; align-items: baseline;
  flex-wrap: wrap; }
.inst-name { font-weight: 700; }
.inst-proto { font: 13px var(--mono); color: var(--dim); }
.fn { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px 10px; margin-top: 8px;
  font: 13px var(--mono); }
.fn .v { text-align: right; font-variant-numeric: tabular-nums; }
.fn .v.bad { color: var(--bad); font-weight: 700; }
.fn .v.ok { color: var(--ok); }
.fn .none { color: var(--dim); }
.driver-code { margin-top: 8px; }
.driver-code summary { font-size: 13px; color: var(--dim); cursor: pointer; }
.driver-code pre { font-size: 13px; line-height: 1.4; background: var(--bg);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; overflow-x: auto;
  margin: 6px 0 0; }
.loghead, .step { display: grid; gap: 2px 12px; padding: 8px;
  grid-template-columns: 52px minmax(0, 1fr) auto;
  grid-template-areas: "n what detail" "n wire wire"; }
.loghead { font-size: 13px; color: var(--dim); text-transform: uppercase; letter-spacing: .06em;
  font-weight: 600; border-bottom: 1px solid var(--line); }
.loghead .n, .step .n { grid-area: n; }
.loghead .who, .step .who { grid-area: who; display: none; }
.loghead .what, .step .what { grid-area: what; }
.loghead .wire, .step .wire { grid-area: wire; }
.loghead .detail, .step .detail { grid-area: detail; text-align: right; }
@media (min-width: 860px) {
  .loghead, .step {
    grid-template-columns: 52px 104px minmax(0, 1.3fr) minmax(0, 1.6fr) minmax(0, 1fr);
    grid-template-areas: "n who what wire detail"; }
  .loghead .who, .step .who { display: block; }
  .loghead .detail, .step .detail { text-align: left; }
}
.step { min-height: 44px; border-bottom: 1px solid var(--line); font-size: 14px;
  align-items: start; font-variant-numeric: tabular-nums; }
.step .n, .step .who { font: 13px var(--mono); color: var(--dim); }
.step .wire { font: 13px var(--mono); color: var(--dim); overflow-wrap: anywhere; }
.step .detail { font: 13px var(--mono); }
.step[data-state~="cur"] { background: color-mix(in srgb, var(--gate) 14%, transparent); }
.step[data-state~="stop"] .what { color: var(--bad); }
.pill { font-size: 13px; font-weight: 700; padding: 2px 8px; border-radius: 999px;
  white-space: nowrap; margin-left: 6px; }
.pill.sent { background: rgba(255,178,36,.15); color: var(--gate); }
.pill.refused { background: rgba(240,82,77,.15); color: var(--bad); }
footer { font-size: 13px; color: var(--dim); text-align: center; }
footer a { color: var(--dim); }
@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; animation: none !important; } }
"""

#: Visible meaning never changes with data -- a fixed line, never derived
#: from the task or the fault (DoD: nothing here can leak it).
SAFETY_LINE = "Simulated instruments only. Nothing here touches real hardware."

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


const NUM_WORDS = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"];

function plural(n, word) { return `${n} ${word}${n === 1 ? "" : "s"}`; }

// "dmm0" -> "DMM", same rule as the data module's `_name_for_address`.
function instName(addr) {
  const a = String(addr || "");
  return a.replace(/\d+$/, "").toUpperCase() || a.toUpperCase();
}

function testPointOf(inst) {
  return inst && inst.probe ? inst.probe.replace(/^card\./, "") : null;
}

// the rail or temp point a probe reads (public card spec), or null.
function specOf(payload, inst) {
  const tp = testPointOf(inst);
  if (!tp) return null;
  const rail = (payload.rails || []).find(r => r.test_point === tp);
  if (rail) return {rail};
  const temp = (payload.temp_points || []).find(t => t.test_point === tp);
  return temp ? {temp} : null;
}

// a reading outside its card's own documented limit -- the public spec,
// never the hidden fault.
function outOfLimit(payload, inst, value) {
  const spec = specOf(payload, inst);
  if (!spec || !Number.isFinite(Number(value))) return false;
  if (spec.rail) return value < spec.rail.lo || value > spec.rail.hi;
  return spec.temp.high_c !== null && spec.temp.high_c !== undefined && value > spec.temp.high_c;
}

function failingAddresses(payload) {
  const out = new Set();
  for (const inst of payload.instruments) {
    const r = latestReading(payload.timeline, inst.address);
    if (r && outOfLimit(payload, inst, r.detail.value)) out.add(inst.address);
  }
  return out;
}

// the agent's own drivers: every instrument it checked, or used a driver on
// (a measure or a call) -- the PSU's drive goes through the SHAL gate, not
// a driver.
function driverCount(payload) {
  const used = new Set(Object.keys(payload.tiles || {}));
  for (const e of payload.timeline) {
    if (["check", "measure", "reading", "failed", "call"].includes(e.kind)) used.add(e.address);
  }
  return used.size;
}

// issue #447: a gate stop is a drive the SHAL gate refused -- counted from
// this run's own timeline, never a fixed number.
function gateStops(payload) {
  return payload.timeline.filter(e => e.kind === "refused").length;
}

function answerLabel(given) { return String(given || "").replace(/_/g, " "); }

// issue #447: the top label row -- the real-run labels (replay or live,
// level, instrument count, "datasheet written by us").
function renderLabels(payload) {
  const n = payload.instruments.length;
  const live = payload.mode === "live";
  const row = document.getElementById("run-labels");
  row.className = "labels" + (live ? " live" : "");
  row.innerHTML = [live ? "LIVE" : "Replay of a recorded run", `${payload.level} level`,
    plural(n, "instrument"), "datasheet written by us"]
    .map(t => `<span>${escapeHtml(t)}</span>`).join(" · ");
}

// issue #447: "Fault found: <part> <what>, <value>, limit <limit>" -- from
// `payload.result` (built server side once the run is closed), every
// value through `escapeHtml`.
function resultLineHtml(result) {
  if (!result) return "";
  const items = result.items.map(i => `<b>${escapeHtml(i.what)}</b>`
    + (i.value ? `, ${escapeHtml(i.value)}` : "")
    + (i.limit ? `, limit ${escapeHtml(i.limit)}` : ""));
  const lead = !items.length ? "Nothing measured"
    : result.found ? "Fault found: " : "No fault found: ";
  return (result.destroyed ? "Card destroyed. " : "") + lead + items.join("; ");
}

function renderVerdict(payload) {
  const el = document.getElementById("verdict-bar");
  if (!payload.closed) {
    el.className = "verdict-bar running";
    el.innerHTML = '<div class="big">Running…</div>'
      + `<span class="reason">${shownSteps(payload.timeline).length} step(s) so far`
      + "</span>";
    return;
  }
  const rec = payload.record || {};
  const destroyed = payload.card.destroyed;
  const disqualified = !!rec.disqualified;
  const correct = !!rec.correct && !destroyed && !disqualified;
  let word = "Correct", cls = "", reason = "";
  if (destroyed) { word = "Wrong"; cls = "bad"; reason = "card destroyed"; }
  else if (disqualified) { word = "Disqualified"; cls = "bad";
    reason = "no measurement was logged"; }
  else if (!correct) { word = "Wrong"; cls = "bad"; }
  // issue #447: the top line is the measured result, then the agent's
  // answer as a chip -- never the bare word alone.
  const line = resultLineHtml(payload.result) || escapeHtml(reason || word);
  const drivers = driverCount(payload);
  el.className = "verdict-bar" + (cls ? " " + cls : "");
  el.innerHTML = `<div class="big">${line}</div>`
    + `<span class="chip ${correct ? "ok" : "bad"}">Agent's answer: `
    + `${escapeHtml(answerLabel(rec.given))} · ${word}</span>`
    + `<span class="chip dim">${drivers ? plural(drivers, "driver") + " written · " : ""}`
    + `${plural(gateStops(payload), "gate stop")}</span>`;
}

// the measured parts so far ("3V3 rail", "regulator temp"), in instrument order.
function measuredParts(payload) {
  const out = [];
  for (const inst of payload.instruments) {
    const spec = specOf(payload, inst);
    if (!spec || !latestReading(payload.timeline, inst.address)) continue;
    out.push(spec.rail ? `${spec.rail.name} rail` : `${spec.temp.name} temp`);
  }
  return out;
}

const FLOW_ICONS = [
  '<rect x="10" y="6" width="24" height="32" rx="2"/><path d="M15 14h14M15 20h14M15 26h9"/>',
  '<path d="M16 12 8 22l8 10M28 12l8 10-8 10M25 9l-6 26"/>',
  '<path d="M8 30a14 14 0 0 1 28 0"/><path d="M22 30 30 18"/><circle cx="22" cy="30" r="2"/>',
  '<path d="M22 6 4 38h36Z"/><path d="M22 17v10M22 32v1"/>',
];

// issue #447: the 4-step strip under the result line -- gets, writes,
// measures, result -- each step's caption from this run's own data.
function renderFlow(payload) {
  const n = payload.instruments.length;
  const drivers = driverCount(payload);
  const parts = measuredParts(payload);
  const res = payload.closed ? payload.result : null;
  const first = res && res.items.length ? res.items[0] : null;
  const cells = [
    {k: "Gets", s: `a card + ${plural(n, "instrument")} it never saw`, done: true},
    {k: "Writes", s: drivers ? plural(drivers, "driver") : "drivers", done: drivers > 0},
    {k: "Measures", s: parts.length ? parts.join(" + ") : "the card", done: parts.length > 0},
    {k: res && res.found ? "Fault" : "Result",
     s: first ? [first.what, first.value, first.limit ? `limit ${first.limit}` : ""]
       .filter(Boolean).join(", ") : "…", done: !!res},
  ];
  let litGiven = false;
  document.getElementById("flow").innerHTML = cells.map((c, i) => {
    let state = c.done ? (i === 3 && res && res.found ? "fault" : "done") : "";
    if (!c.done && !litGiven && !payload.closed) { state = "lit"; litGiven = true; }
    return `<div class="st ${state}${payload.closed ? " seq" : ""}" style="--i:${i}">`
      + `<svg viewBox="0 0 44 44" aria-hidden="true">${FLOW_ICONS[i]}</svg>`
      + `<div class="k">${escapeHtml(c.k)}</div><div class="s">${escapeHtml(c.s)}</div></div>`;
  }).join("");
}

function arrowMarkers() {
  return "<defs>" + [["dim", "var(--line)"], ["lit", "var(--gate)"], ["bad", "var(--bad)"]]
    .map(([id, c]) => `<marker id="ah-${id}" viewBox="0 0 10 10" refX="9" refY="5" `
      + `markerWidth="6" markerHeight="6" orient="auto-start-reverse">`
      + `<path d="M0 0 10 5 0 10Z" style="fill:${c}"/></marker>`).join("") + "</defs>";
}

// kinds where an instrument acts on the card itself (powers, switches, reads it)
const CARD_KINDS = new Set(["write", "refused", "protection", "damage", "call", "reading",
  "measure", "failed"]);

// issue #447: the one live visual at the top -- the agent, its instruments
// and the card, with arrows that light up in the order the agent used each
// instrument (agent -> instrument: it wrote and used a driver; instrument
// -> card: it powered, switched or measured the card), and the result big.
function renderHero(payload) {
  const insts = payload.instruments;
  const steps = shownSteps(payload.timeline);
  const order = [];
  for (const e of steps) if (e.address && !order.includes(e.address)) order.push(e.address);
  const touched = new Set(steps.filter(e => CARD_KINDS.has(e.kind)).map(e => e.address));
  const failing = failingAddresses(payload);
  const newest = !payload.closed && steps.length ? steps[steps.length - 1].address : null;
  const rowH = 60, h = Math.max(insts.length * rowH, 140) + 8, cy = h / 2;
  const top = (h - insts.length * rowH) / 2 + 6;
  let svg = `<svg viewBox="0 0 480 ${h}" role="img" `
    + `aria-label="The agent, its instruments and the card">${arrowMarkers()}`;
  insts.forEach((inst, k) => {
    const y = top + k * rowH, my = y + 24, i = order.indexOf(inst.address);
    const seq = payload.closed && i >= 0 ? ` seq" style="--i:${i}` : "";
    const a1 = i >= 0 ? "lit" : "dim";
    const a2 = failing.has(inst.address) ? "bad" : touched.has(inst.address) ? "lit" : "dim";
    svg += `<path class="arrow ${a1 === "dim" ? "" : a1}${seq}" d="M100 ${cy} L180 ${my}" `
      + `marker-end="url(#ah-${a1})"/>`
      + `<path class="arrow ${a2 === "dim" ? "" : a2}${seq}" d="M298 ${my} L370 ${cy}" `
      + `marker-end="url(#ah-${a2})"/>`
      + `<rect class="inst-box${inst.address === newest ? " active" : ""}" x="184" y="${y}" `
      + `width="112" height="48" rx="8"/>`
      + `<text x="240" y="${y + 31}" text-anchor="middle">${escapeHtml(instName(inst.address))}`
      + "</text>";
  });
  svg += `<rect class="agent-box" x="8" y="${cy - 30}" width="92" height="60" rx="10"/>`
    + `<text x="54" y="${cy + 7}" text-anchor="middle">Agent</text>`
    + `<rect class="blk${payload.card.destroyed || failing.size ? " hot" : ""}" x="372" `
    + `y="${cy - 36}" width="100" height="72" rx="10"/>`
    + `<text x="422" y="${cy + 7}" text-anchor="middle">Card</text></svg>`;
  document.getElementById("hero").innerHTML = svg;

  const big = document.getElementById("hero-result");
  if (!payload.closed || !payload.record) {
    big.className = "hero-result";
    big.textContent = "…";
    return;
  }
  const rec = payload.record;
  const ok = !!rec.correct && !payload.card.destroyed && !rec.disqualified;
  big.className = "hero-result " + (ok ? "ok" : "bad");
  big.textContent = `${answerLabel(rec.given)} ${ok ? "✓" : "✕"}`;
}

// issue #447: the card section is the run's own card yaml -- its id,
// inputs and the documented limits of each measure point.
function cardSpecText(payload) {
  const parts = [`Card ${payload.card_id || ""}`.trim()];
  for (const i of payload.inputs || []) parts.push(`${i.name.toUpperCase()} ${i.nominal_v} V`);
  for (const r of payload.rails || []) {
    if (r.lo !== undefined) parts.push(`${r.name} rail ${r.lo.toFixed(2)}-${r.hi.toFixed(2)} V`);
  }
  for (const t of payload.temp_points || []) parts.push(`${t.name} limit ${t.high_c} °C`);
  return parts.join(" · ");
}

// issue #447: the card's block diagram, drawn from the card yaml's own
// `blocks:` field (never by hand per card): each block in order, its links
// (`from`), and where each instrument attaches -- a `drives:` instrument on
// the block with that `input`, a `switches:` one on the block that
// `switch`es it, a `probe:` one on the measure point (`test_point`).
function renderDiagram(payload) {
  document.getElementById("card-spec").textContent = cardSpecText(payload);
  const blocks = payload.card_blocks || [];
  const box = document.getElementById("card-diagram");
  if (!blocks.length) { box.innerHTML = ""; return; }
  const W = 120, BW = 104, x = k => 8 + k * W, cx = k => x(k) + BW / 2;
  const idx = Object.fromEntries(blocks.map((b, k) => [b.id, k]));
  const steps = shownSteps(payload.timeline);
  const newest = !payload.closed && steps.length ? steps[steps.length - 1].address : null;
  const failing = failingAddresses(payload);
  const width = Math.max(480, x(blocks.length) + 8);
  let svg = `<svg viewBox="0 0 ${width} 266" role="img" `
    + `aria-label="Block diagram of the card, with the instruments attached">`
    + `<rect class="outline" x="2" y="66" width="${width - 4}" height="132" rx="10"/>`;
  blocks.forEach((b, k) => {
    if (b.from === null || b.from === undefined || !(b.from in idx)) return;
    const f = idx[b.from];
    svg += f === k - 1
      ? `<path class="net" d="M${x(f) + BW} 116 H${x(k)}"/>`
      : `<path class="net" d="M${cx(f)} 144 V160 H${cx(k)} V144"/>`;
  });
  for (const inst of payload.instruments) {
    const ref = (inst.drives || inst.switches || inst.probe || "").replace(/^card\./, "");
    const k = blocks.findIndex(b => inst.drives ? b.input === ref
      : inst.switches ? b.switch === ref : b.test_point === ref);
    if (k < 0) continue;
    const act = inst.address === newest ? " active" : "";
    const name = escapeHtml(inst.address);
    if (inst.probe) {
      const end = k > 0;
      svg += `<path class="probe" d="M${cx(k)} 144 V214"/>`
        + `<circle class="tp" cx="${cx(k)}" cy="144" r="6"/>`
        + `<text class="tp-label" x="${cx(k) + (end ? -10 : 10)}" y="186" `
        + `text-anchor="${end ? "end" : "start"}">${escapeHtml(ref)}</text>`
        + `<rect class="inst-box${act}" x="${x(k)}" y="214" width="${BW}" height="44" rx="8"/>`
        + `<text x="${cx(k)}" y="243" text-anchor="middle">${name}</text>`;
    } else {
      svg += `<path class="net" d="M${cx(k)} 48 V88"/>`
        + `<rect class="inst-box${act}" x="${x(k)}" y="4" width="${BW}" height="44" rx="8"/>`
        + `<text x="${cx(k)}" y="33" text-anchor="middle">${name}</text>`;
    }
  }
  const tpHot = new Set();
  for (const inst of payload.instruments) {
    if (failing.has(inst.address)) tpHot.add(testPointOf(inst));
  }
  blocks.forEach((b, k) => {
    const applied = b.input && payload.card.applied && payload.card.applied[b.input];
    let cls = "blk";
    if (b.test_point && tpHot.has(b.test_point)) cls += " hot";
    else if (b.switch && payload.card.power_on && steps.some(e => e.kind === "call")) cls += " on";
    else if (applied) cls += " on";
    svg += `<rect class="${cls}" x="${x(k)}" y="88" width="${BW}" height="56" rx="6"/>`
      + `<text x="${cx(k)}" y="123" text-anchor="middle">${escapeHtml(b.label)}</text>`;
  });
  box.innerHTML = svg + "</svg>";
}

// issue #447: one card per instrument -- each function the agent used on
// it, and the value each one returned, grouped from the run timeline.
function fnRow(payload, inst, e) {
  const d = e.detail || {}, tp = testPointOf(inst);
  const at = tp ? ` @${tp}` : "";
  if (e.kind === "check") return {fn: "check driver", v: d.passed ? "passed" : "failed",
    cls: d.passed ? "ok" : "bad"};
  if (e.kind === "write" && d.volts !== undefined) return {fn: stepTitle(e), v: fmtNum(d.volts)};
  if (e.kind === "refused") return {fn: `drive ${fmtNum(d.volts)}`, v: "refused by the gate",
    cls: "bad"};
  if (e.kind === "protection") return {fn: `drive ${fmtNum(d.volts)}`, v: "protection tripped",
    cls: "bad"};
  if (e.kind === "damage") return {fn: `drive ${fmtNum(d.volts)}`, v: "card destroyed",
    cls: "bad"};
  if (e.kind === "call") return {fn: `${d.op}(${(d.args || []).join(", ")})`,
    v: d.ok ? "ok" : "failed", cls: d.ok ? "" : "bad"};
  if (e.kind === "reading") return {fn: `measure${at}`, v: fmtNum(d.value, d.unit),
    cls: outOfLimit(payload, inst, d.value) ? "bad" : "ok"};
  if (e.kind === "measure") return {fn: `measure${at}`, v: "attempted"};
  if (e.kind === "failed") return {fn: `measure${at}`, v: `failed (${d.cause || "error"})`,
    cls: "bad"};
  return {fn: stepTitle(e), v: stepDetail(e)};
}

function driverFor(payload, inst) {
  const drivers = payload.drivers || {};
  for (const key of [inst.address, inst.case, instName(inst.address).toLowerCase()]) {
    if (key && drivers[key]) return [key, drivers[key]];
  }
  return null;
}

function driverHtml(label, d) {
  return `<details class="driver-code"><summary>${escapeHtml(label)}, ${d.lines} lines</summary>`
    + `<pre>${escapeHtml(d.code)}</pre></details>`;
}

function renderInstruments(payload) {
  const steps = shownSteps(payload.timeline);
  const newest = !payload.closed && steps.length ? steps[steps.length - 1].address : null;
  const matched = new Set();
  let out = payload.instruments.map(inst => {
    const rows = steps.filter(e => e.address === inst.address).map(e => fnRow(payload, inst, e));
    const fns = rows.length ? rows.map(r => `<span>${escapeHtml(r.fn)}</span>`
      + `<span class="v${r.cls ? " " + r.cls : ""}">${escapeHtml(r.v)}</span>`).join("")
      : '<span class="none">not used</span>';
    const tile = (payload.tiles || {})[inst.address];
    const drv = driverFor(payload, inst);
    if (drv) matched.add(drv[0]);
    const check = tile ? ` · check ${tile.passed ? "passed" : "failed"}` : "";
    return `<div class="inst${inst.address === newest ? " active" : ""}">`
      + `<div class="inst-head"><span class="inst-name">${escapeHtml(inst.address)} · `
      + `${escapeHtml(instrumentRoleText(payload, inst))}</span>`
      + `<span class="inst-proto">${escapeHtml(inst.protocol || "")}</span></div>`
      + `<div class="fn">${fns}</div>`
      + (drv ? driverHtml(`Driver written by the agent${check}`, drv[1]) : "")
      + "</div>";
  }).join("");
  for (const [name, d] of Object.entries(payload.drivers || {})) {
    if (!matched.has(name)) out += driverHtml(`The driver the agent wrote for the ${name}`, d);
  }
  document.getElementById("instruments").innerHTML = out;
}

// issue #447: the #457 log, placed in the layout -- one row per step the
// agent took (the same rows `shownSteps` gives), each with the real bus
// exchange(s) it made, read from the bus-layer log rows in the timeline
// (never built or guessed here). A bus row is attached to the next shown
// step at its address (or, if none follows, the last one).
function isBusRow(e) {
  return e.kind === "exchange" || ((e.kind === "query" || e.kind === "write")
    && !!e.detail && e.detail.cmd !== undefined);
}

function wireMap(all) {
  const shown = new Set(shownSteps(all));
  const map = new Map(), pending = {}, last = {};
  for (const e of all) {
    if (shown.has(e)) {
      map.set(e, (pending[e.address] || []).concat(isBusRow(e) ? [e] : []));
      pending[e.address] = [];
      last[e.address] = e;
    } else if (isBusRow(e)) {
      (pending[e.address] = pending[e.address] || []).push(e);
    }
  }
  for (const addr of Object.keys(pending)) {
    if (pending[addr].length && last[addr]) {
      map.set(last[addr], map.get(last[addr]).concat(pending[addr]));
    }
  }
  return map;
}

function busText(v, family) {
  if (family === "sim_i2c" && typeof v === "string" && /^([0-9a-f]{2})+$/i.test(v)) {
    return v.match(/../g).join(" ").toUpperCase();
  }
  if (v && typeof v === "object" && !Array.isArray(v)) {
    return "{" + Object.entries(v).map(([k, x]) =>
      `${k}:${x !== null && typeof x === "object" ? JSON.stringify(x) : x}`).join(", ") + "}";
  }
  return typeof v === "string" ? v : JSON.stringify(v);
}

function wireText(b) {
  const d = b.detail || {};
  if (b.kind !== "exchange") {
    return d.reply === undefined || d.reply === "" ? d.cmd : `${d.cmd} → ${d.reply}`;
  }
  const req = busText(d.request, d.bus_family);
  const empty = d.response === undefined || d.response === null || d.response === "";
  return empty ? req : `${req} → ${busText(d.response, d.bus_family)}`;
}

function relTime(ts, t0) {
  const s = Math.round((Date.parse(ts) - Date.parse(t0)) / 1000);
  if (!Number.isFinite(s) || s < 0) return "";
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function replyText(e) {
  if (e.kind === "write" && e.detail && e.detail.volts !== undefined) return fmtNum(e.detail.volts);
  return stepDetail(e);
}

function stepRowHtml(e, i, wires, t0, cur) {
  const title = escapeHtml(stepTitle(e));
  const detail = escapeHtml(replyText(e));
  const wire = (wires || []).map(wireText).join("; ");
  const pill = e.kind === "refused" ? '<span class="pill refused">refused</span>'
    : (e.kind === "protection" || e.kind === "damage")
      ? '<span class="pill sent">sent</span>' : "";
  const state = [e.kind === "refused" ? "stop" : "", cur ? "cur" : ""].filter(Boolean).join(" ");
  return `<div class="step" data-i="${i}"${state ? ` data-state="${state}"` : ""}>`
    + `<div class="n">${escapeHtml(t0 ? relTime(e.ts, t0) : "")}</div>`
    + `<div class="who">${escapeHtml(e.address || "")}</div>`
    + `<div class="what">${title}</div>`
    + `<div class="wire">${escapeHtml(wire || "—")}</div>`
    + `<div class="detail">${detail}${pill}</div></div>`;
}

function renderTimeline(payload) {
  const list = document.getElementById("timeline-list");
  const all = payload.log_timeline || payload.timeline;
  const wires = wireMap(all);
  const t0 = all.length ? all[0].ts : null;
  const agentSteps = shownSteps(payload.timeline);
  list.innerHTML = agentSteps.map((e, i) => stepRowHtml(e, i, wires.get(e), t0,
    !payload.closed && i === agentSteps.length - 1)).join("");
}

// issue #457 (scope added): the page shows each instrument's role as
// plain words BUILT from the task yaml's own `drives:`/`probe:`/`switches:` field and
// the rail/temp-point data already in the payload -- "powers VIN",
// "measures the 3V3 rail", "measures the regulator temperature" -- never
// hand-written text. The agent-facing JSON (`payload.instruments[].role`,
// `drives`/`probe` themselves) is untouched; this is display only.
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

// the export's own replay label is fixed in the static footer
// (`data-fixed`); the live page builds its own from the payload.
function renderFooter(payload) {
  const el = document.getElementById("run-label");
  if (el.getAttribute("data-fixed")) return;
  const mode = payload.closed ? "Replay of a recorded run" : "Live run";
  el.textContent = `${mode} · seed ${payload.seed}`;
}

// the replay reads every bus row, not just the rows shown so far.
function withLog(payload) {
  return Object.assign({}, payload, {log_timeline: payload.log_timeline || payload.timeline});
}

function renderReplayButton(payload) {
  const btn = document.getElementById("replay");
  const can = payload.closed && typeof startExport === "function";
  btn.hidden = !can;
  btn.onclick = can ? () => startExport(withLog(payload)) : null;
}

function render(payload) {
  // issue #447 page text: the title is the task's own question, then one line.
  document.getElementById("question").textContent = payload.question || payload.title || "";
  const n = payload.instruments.length;
  document.getElementById("lede").textContent = `An AI agent got this card and `
    + `${NUM_WORDS[n] || n} instrument${n === 1 ? "" : "s"} it had never seen. It wrote a driver `
    + "for each one, measured, and named the fault.";
  renderLabels(payload);
  renderVerdict(payload);
  renderFlow(payload);
  renderHero(payload);
  renderDiagram(payload);
  renderInstruments(payload);
  renderTimeline(payload);
  renderFooter(payload);
  renderReplayButton(payload);
}

function withMode(payload) {
  // the live page's own label: LIVE while the run is open, the same
  // replay label as an export once it closes.
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


def _shell(run_id: str, *, banner: str = "") -> str:
    """The static document both the live page and the export embed their
    data into -- same markup, same ids, so `render` works unchanged in both.
    `banner` is the export's own replay label, already HTML-escaped (the
    live page builds its own in `renderFooter`)."""
    fixed = ' data-fixed="1"' if banner else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>shal-arena &middot; Watch</title>
<style>{_STYLE}</style>
</head>
<body>
<div class="page">
  <div class="labels" id="run-labels">Replay of a recorded run</div>
  <header>
    <h1 id="question"></h1>
    <p class="lede" id="lede"></p>
  </header>
  <section class="hero" aria-label="What happened">
    <div class="verdict-bar" id="verdict-bar"></div>
    <div class="flow" id="flow"></div>
    <div class="box hero-visual">
      <div class="viz" id="hero"></div>
      <div class="hero-result" id="hero-result"></div>
    </div>
  </section>
  <div class="controls">
    <button id="replay" type="button" hidden>&#9654; Replay the run</button>
  </div>
  <div class="grid">
    <section class="box">
      <h2>The card</h2>
      <div class="card-spec mono" id="card-spec"></div>
      <div class="viz" id="card-diagram"></div>
    </section>
    <section class="box">
      <h2>Instruments and what the agent used</h2>
      <div class="inst-list" id="instruments"></div>
    </section>
  </div>
  <section class="box">
    <h2>Log of the run</h2>
    <div class="loghead"><span class="n">Time</span><span class="who">Instrument</span>
      <span class="what">Function</span><span class="wire">Sent on the wire</span>
      <span class="detail">Reply</span></div>
    <div id="timeline-list"></div>
  </section>
  <footer>{SAFETY_LINE} &middot; <span id="run-label"{fixed}>{banner}</span>
    &middot; <a href="{REPO_URL}">github.com/determlab/shal</a>
  </footer>
</div>
</body>
</html>
"""


def render_watch_page(run_id: str, payload: dict[str, Any]) -> str:
    """The live page: embeds `payload` once (as data, in a JSON script tag --
    never pre-rendered fault text), then polls `/api/run/<run_id>` every
    second via `_SCRIPT`'s own `start()` until the run closes. The export's
    own `startExport` comes along too, for the closed page's replay button."""
    from .export import _EXPORT_SCRIPT  # export.py imports this module

    payload_json = safe_json(payload)
    shell = _shell(run_id)
    script_tag = (
        f'<script id="run-data" type="application/json">{payload_json}</script>\n'
        f"<script>{_SCRIPT}\n{_EXPORT_SCRIPT}\n"
        f"start({safe_json(run_id)}, JSON.parse(document.getElementById('run-data')"
        f".textContent));</script>\n"
    )
    return shell.replace("</body>", script_tag + "</body>")
