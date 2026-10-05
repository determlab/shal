"""Rack page (issue #315, the later part): instruments as tiles on a shelf;
drag a tile that has a driver into a rack slot; `setup.yaml` is built as you
go, with download and copy. The page does nothing that cannot be done without
it: `setup_yaml(slots)` is the same text, and `shal` loads it.
"""
from __future__ import annotations

import html
import json
from typing import Any

SLOTS = 4
ISSUE_URL = ("https://github.com/determlab/shal/issues/new?labels=driver-request"
             "&title=Driver+request%3A+")

# id, title, compatible (None: no driver yet)
INSTRUMENTS: list[dict[str, Any]] = [
    {"id": "psu0", "title": "Bench PSU", "driver": "arena,bench-psu1"},
    {"id": "dmm0", "title": "Bench DMM", "driver": "arena,bench-dmm1"},
    {"id": "scope0", "title": "Oscilloscope", "driver": None},
    {"id": "fgen0", "title": "Function generator", "driver": None},
]


def setup_yaml(slots: list[dict[str, Any] | None]) -> str:
    """The topology for the filled slots: one sim SCPI bus, one node per slot.
    The JS on the page builds exactly this text."""
    lines = ["shal_version: 1", "root:", "  bench:", "    id: bench",
             "    driver: shal,sim-scpi", "    address: sim0", "    children:"]
    filled = [s for s in slots if s]
    if not filled:
        lines[-1] = "    children: {}"
    for n, s in enumerate(filled, start=1):
        lines.append(f'      {s["id"]}: {{id: {s["id"]}, driver: "{s["driver"]}", address: {n}}}')
    return "\n".join(lines) + "\n"


_CSS = """
body{font:16px system-ui,sans-serif;margin:0;background:#101418;color:#e8eaed}
main{max-width:760px;margin:2rem auto;padding:0 1rem}
.row{display:flex;gap:.6rem;flex-wrap:wrap;margin:.6rem 0}
.tile,.slot{width:140px;min-height:70px;padding:.5rem;border:1px solid #888;border-radius:6px}
.tile.nodriver{background:#444;color:#aaa}
.tile[draggable=true]{cursor:grab;background:#1e3a5f}
.slot{border-style:dashed}
pre{background:#000;padding:.6rem;min-height:5rem}
button{font:inherit;padding:.4rem .9rem;margin-right:.5rem;cursor:pointer}
a{color:#8ab4f8}
"""

_JS = """
var slots=[null,null,null,null];
function build(){var f=slots.filter(Boolean);
var o='shal_version: 1\\nroot:\\n  bench:\\n    id: bench\\n    driver: shal,sim-scpi\\n'+
'    address: sim0\\n    children:'+(f.length?'':' {}')+'\\n';
f.forEach(function(s,i){o+='      '+s.id+': {id: '+s.id+', driver: "'+s.driver+
'", address: '+(i+1)+'}\\n'});return o}
function show(){document.getElementById('out').textContent=build();
slots.forEach(function(s,i){document.getElementById('slot'+i).textContent=
s?s.title:'empty slot'})}
document.querySelectorAll('.tile[draggable=true]').forEach(function(t){
t.ondragstart=function(ev){ev.dataTransfer.setData('text/plain',t.dataset.i)}});
document.querySelectorAll('.slot').forEach(function(el,i){
el.ondragover=function(ev){ev.preventDefault()};
el.ondrop=function(ev){ev.preventDefault();
slots[i]=INSTRUMENTS[+ev.dataTransfer.getData('text/plain')];show()}});
document.getElementById('copy').onclick=function(){
navigator.clipboard&&navigator.clipboard.writeText(build())};
document.getElementById('dl').onclick=function(){
var a=document.createElement('a');
a.href=URL.createObjectURL(new Blob([build()],{type:'text/yaml'}));
a.download='setup.yaml';a.click()};
show();
"""


def render_rack() -> str:
    e = html.escape
    tiles = []
    for i, ins in enumerate(INSTRUMENTS):
        if ins["driver"]:
            tiles.append(f'<div class="tile" draggable="true" data-i="{i}">{e(ins["title"])}</div>')
        else:
            link = ISSUE_URL + e(ins["title"].replace(" ", "+"))
            tiles.append(f'<div class="tile nodriver">{e(ins["title"])}<br>'
                         f'<a href="{link}" target="_blank" rel="noopener">Request a driver</a>'
                         "</div>")
    tiles.append(f'<div class="tile nodriver"><a href="{ISSUE_URL}" target="_blank" '
                 'rel="noopener">My instrument is not here</a></div>')
    slots = "".join(f'<div class="slot" id="slot{i}">empty slot</div>' for i in range(SLOTS))
    return "\n".join([
        "<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
        "<title>SHAL Arena rack</title>", f"<style>{_CSS}</style></head><body><main>",
        "<h1>Rack</h1><h2>Shelf</h2>", f'<div class="row">{"".join(tiles)}</div>',
        "<h2>Rack</h2>", f'<div class="row">{slots}</div>',
        "<h2>setup.yaml</h2>", '<pre id="out"></pre>',
        '<button id="copy">Copy</button><button id="dl">Download setup.yaml</button>',
        f"<script>var INSTRUMENTS={json.dumps(INSTRUMENTS)};{_JS}</script>",
        "</main></body></html>"])
