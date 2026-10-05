"""Result card and replay (issue #315): one offline HTML file made from the
run record and the score file, adding nothing of its own.

`card_data(record, score)` is the same data as a plain dict (the agent path:
no page needed). A record that is not closed yet — no ``closed_at``, or no
score — yields no fault information at all: not in the data, not in the html.

Turns (ruling on #315): ``check``, ``measure`` and ``drive`` calls count; the
``answer`` does not; a gate-refused call is shown in red and still counts.
"""
from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

REPO_LINK = "github.com/determlab/shal"
TURN_KINDS = ("check", "measure", "drive")


def _is_closed(record: dict[str, Any], score: dict[str, Any] | None) -> bool:
    return bool(record.get("closed_at")) and score is not None


def card_data(record: dict[str, Any], score: dict[str, Any] | None = None) -> dict[str, Any]:
    closed = _is_closed(record, score)
    calls = [{"n": i, "kind": c.get("kind", "turn"), "address": c.get("address"),
              "refused": bool(c.get("refused", False))}
             for i, c in enumerate(record.get("calls", []), start=1)
             if c.get("kind") in TURN_KINDS]
    data: dict[str, Any] = {
        "closed": closed,
        "label": "replay",
        "run_id": record.get("run_id"),
        "seed": score["seed"] if score else record.get("seed"),
        "calls": calls,
        "turns": len(calls),
        "gate_stops": sum(1 for c in calls if c["refused"]),
        "bottom_line": "",
    }
    if closed:
        assert score is not None
        caught, total, ff = score["faults_caught"], score["faults_total"], score["false_fails"]
        headline = f"{caught} of {total} faults caught. {ff} false fails."
        if ff > 0:
            headline += f" The card was fine; {ff} fault call(s) were wrong."
        data.update(
            headline=headline, faults_caught=caught, faults_total=total, false_fails=ff,
            result={"given": record.get("given"), "correct": record.get("correct")},
            fault_type=score["fault_type"], game_version=score["game_version"],
            record_sha256=score["record_sha256"], record_sha256_short=score["record_sha256"][:12],
            duration_s=score["duration_s"])
    else:
        data.update(headline="Run in progress. The card appears when the run ends.",
                    result=None)
    data["bottom_line"] = f"replay · simulated · seed {data['seed']}"
    return data


def copy_text(data: dict[str, Any]) -> str:
    return f"{data['headline']} {data['turns']} turns. {data['bottom_line']} {REPO_LINK}"


_CSS = """
body{font:16px system-ui,sans-serif;margin:0;background:#101418;color:#e8eaed}
main{max-width:720px;margin:2rem auto;padding:0 1rem}
.tag{display:inline-block;border:1px solid #888;padding:.1rem .5rem;border-radius:4px;
font-size:.8rem;text-transform:uppercase}
h1{font-size:1.5rem;margin:.6rem 0}
ol{padding-left:1.4rem}li{margin:.25rem 0;font-family:ui-monospace,monospace}
li.red{color:#ff6b6b;font-weight:bold}
.small{font-size:.8rem;color:#9aa0a6}
button{font:inherit;padding:.4rem .9rem;margin-right:.5rem;cursor:pointer}
a{color:#8ab4f8}
"""

_JS = """
function line(){return document.getElementById('headline').textContent+' '+
document.getElementById('turns').textContent+' '+
document.getElementById('bottom').textContent+' '+document.getElementById('copy').dataset.link}
document.getElementById('copy').onclick=function(){
var t=line();
if(navigator.clipboard){navigator.clipboard.writeText(t)}
else{var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);
a.select();document.execCommand('copy');a.remove()}};
document.getElementById('save').onclick=function(){
var c=document.createElement('canvas');c.width=1200;c.height=630;
var x=c.getContext('2d');x.fillStyle='#101418';x.fillRect(0,0,1200,630);
x.fillStyle='#e8eaed';x.font='bold 40px sans-serif';
x.fillText(document.getElementById('headline').textContent,60,260,1080);
x.font='32px sans-serif';
x.fillText(document.getElementById('turns').textContent,60,340);
x.fillText(document.getElementById('bottom').textContent,60,520);
x.fillText(document.getElementById('copy').dataset.link,60,570);
c.toBlob(function(b){var a=document.createElement('a');a.href=URL.createObjectURL(b);
a.download='shal-arena-card.png';a.click()})};
"""


def render_card(record: dict[str, Any], score: dict[str, Any] | None = None) -> str:
    d = card_data(record, score)
    e = html.escape
    items = []
    for c in d["calls"]:
        text = f"{c['n']}. {c['kind']} {c['address'] or ''}".strip()
        if c["refused"]:
            items.append(f'<li class="red">{e(text)} - refused at the gate (nothing sent)</li>')
        else:
            items.append(f"<li>{e(text)}</li>")
    parts = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>SHAL Arena replay</title>",
        f"<style>{_CSS}</style></head><body><main>",
        '<span class="tag">Replay</span>',
        f'<h1 id="headline">{e(d["headline"])}</h1>',
        f'<p id="turns">{d["turns"]} turns</p>',
        "<ol>" + "".join(items) + "</ol>",
    ]
    if d["closed"]:
        r = d["result"]
        parts.append(f'<p>Result: answered {e(str(r["given"]))} - '
                     f'{"correct" if r["correct"] else "wrong"}</p>')
        parts.append(f'<p class="small">fault_type {e(d["fault_type"])} · game_version '
                     f'{e(d["game_version"])} · record {e(d["record_sha256_short"])}</p>')
    parts.append(f'<p id="bottom">{e(d["bottom_line"])}</p>')
    parts.append(
        '<p><button id="copy" data-link="' + REPO_LINK + '">Copy result</button>'
        '<button id="save">Save as image</button></p>')
    parts.append('<p class="small"><a href="https://' + REPO_LINK + '">' + REPO_LINK + "</a></p>")
    parts.append(f"<script>{_JS}</script></main></body></html>")
    return "\n".join(parts)


def write_card(record_path: str | Path, score_path: str | Path, out: str | Path) -> Path:
    """Read ``<run_id>.record.json`` and ``<run_id>.score.json`` and write the card."""
    record = json.loads(Path(record_path).read_text(encoding="utf-8"))
    score = json.loads(Path(score_path).read_text(encoding="utf-8"))
    out = Path(out)
    out.write_text(render_card(record, score), encoding="utf-8")
    return out
