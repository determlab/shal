"""Rack page (issue #315 Scope): instrument tiles on a shelf that can be
dragged into rack slots, building a `setup.yaml` SHAL topology.

The tiles are the arena's own ADK case catalogue (`cases.CASES`) — the exact
same cases `shal-arena check-driver` validates a player's driver against —
so "simulators only, nothing touches real hardware" (the rule for all arena
work) extends to this page too: every tile a player can actually drag in has
a working sim twin, and the topology it produces loads for real, right now,
with no hardware and no extra `--drivers` file.

A case with no packaged `harness/sim.py` yet renders grey, with a link to
file a `driver-request` issue instead of a rack slot — this never happens
for either of the two cases packaged today, but the mechanism holds for
every case `cases.py` adds later without this file changing.

"The page does nothing that cannot also be done without it" (issue #315
Scope): `build_setup_yaml` is the whole mechanism; `render_rack_page` is
only a way for a human to drive it by dragging tiles instead of calling it.
The page's own embedded script performs the same assembly `build_setup_yaml`
does below, so the two must be read together — this module is their single
source of truth."""
from __future__ import annotations

import html
import json
from dataclasses import dataclass

import yaml

from ..card_sim import catalogue as _instrument_catalogue
from ..cases import CASES, resolve_case

DRIVER_REQUEST_URL = "https://github.com/determlab/shal/issues/new?labels=driver-request"
NOT_HERE_URL = "https://github.com/determlab/shal/issues/new?labels=driver-request"


@dataclass(frozen=True)
class Tile:
    case: str
    compatible: str
    replacement_usd: float | None
    has_driver: bool


def rack_tiles() -> list[Tile]:
    """One tile per packaged ADK case (`cases.CASES`), in a stable order. A
    case whose `harness/sim.py` is not packaged yet (none, today) gets
    `has_driver=False` — the grey "Request a driver" tile."""
    cat = _instrument_catalogue()
    out = []
    for name in sorted(CASES):
        case = CASES[name]
        spec = cat.get(name)
        out.append(Tile(
            case=name,
            compatible=case.compatible,
            replacement_usd=spec.replacement_usd if spec else None,
            has_driver=case.harness_topology.is_file(),
        ))
    return out


def build_setup_yaml(case_names: list[str]) -> str:
    """The `setup.yaml` a rack of ``case_names`` builds — one `shal,sim-scpi`
    bench per slot, each carrying one node of that case's own `compatible`
    driver (the same one `shal-arena check-driver` binds against). Raises
    `shal_arena.errors.TaskFormatError` (via `resolve_case`) on an unknown
    case name, naming the fix, same as every other arena entry point."""
    if not case_names:
        raise ValueError("pick at least one instrument tile for the rack")
    root: dict[str, dict] = {}
    for i, name in enumerate(case_names):
        case = resolve_case(name)  # unknown name -> TaskFormatError with a fix
        node_id = name.replace("-", "_")
        root[f"bench{i}"] = {
            "driver": "shal,sim-scpi",
            "address": f"sim{i}",
            "children": {
                node_id: {"driver": case.compatible, "address": 1},
            },
        }
    doc = {"shal_version": 1, "root": root}
    return yaml.safe_dump(doc, sort_keys=False)


def _tile_html(tile: Tile) -> str:
    label = html.escape(tile.case)
    compatible = html.escape(tile.compatible)
    cost = f"${tile.replacement_usd:,.0f}" if tile.replacement_usd is not None else ""
    if tile.has_driver:
        return (
            f'<div class="tile" draggable="true" data-case="{label}">'
            f'<div class="tile-name">{label}</div>'
            f'<div class="tile-compatible">{compatible}</div>'
            f'<div class="tile-cost">{html.escape(cost)}</div>'
            f"</div>"
        )
    return (
        f'<div class="tile tile-grey" data-case="{label}">'
        f'<div class="tile-name">{label}</div>'
        f'<div class="tile-compatible">{compatible}</div>'
        f'<a class="request-driver" href="{DRIVER_REQUEST_URL}" '
        f'target="_blank" rel="noopener">Request a driver</a>'
        f"</div>"
    )


# Kept in lockstep with `build_setup_yaml` above by hand: a drag in the
# browser calls this, `build_setup_yaml` is what a pytest calls on the exact
# same case list, so the two must never drift (see module docstring).
_JS_BUILD_SETUP_YAML = """
function buildSetupYaml(caseNames, compatByCase) {
  if (!caseNames.length) return "";
  const lines = ["shal_version: 1", "root:"];
  caseNames.forEach((name, i) => {
    const nodeId = name.replace(/-/g, "_");
    const compatible = compatByCase[name];
    lines.push(`  bench${i}:`);
    lines.push(`    driver: shal,sim-scpi`);
    lines.push(`    address: sim${i}`);
    lines.push(`    children:`);
    lines.push(`      ${nodeId}:`);
    lines.push(`        driver: "${compatible}"`);
    lines.push(`        address: 1`);
  });
  return lines.join("\\n") + "\\n";
}
"""


def render_rack_page(tiles: list[Tile] | None = None) -> str:
    """The rack page: tiles on a shelf, five rack slots to drag them into, a
    live `setup.yaml` preview with Download and Copy. Not constrained to the
    result card's "one http link, no scripts" rule (that rule is scoped to
    the card in issue #315 Scope) — this page's own inline script is the
    only script, and the only outbound links are the explicit
    `driver-request` issue links the Scope calls for."""
    tiles = rack_tiles() if tiles is None else tiles
    compat_by_case = {t.case: t.compatible for t in tiles}
    tiles_html = "\n".join(_tile_html(t) for t in tiles)
    compat_json = json.dumps(compat_by_case)
    not_here_url = html.escape(NOT_HERE_URL)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SHAL Arena — rack</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         margin: 0; padding: 24px; background: #0b0f14; color: #e6edf3; }}
  h1 {{ font-size: 20px; }}
  .shelf, .rack {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0 24px; }}
  .tile {{ border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px;
          background: #161b22; cursor: grab; min-width: 140px; }}
  .tile-grey {{ opacity: 0.6; cursor: default; }}
  .tile-name {{ font-weight: 600; }}
  .tile-compatible, .tile-cost {{ font-size: 12px; color: #8b949e; }}
  .request-driver {{ display: inline-block; margin-top: 6px; font-size: 12px; }}
  .slot {{ width: 160px; min-height: 64px; border: 2px dashed #30363d; border-radius: 8px;
          display: flex; align-items: center; justify-content: center; font-size: 12px;
          color: #8b949e; }}
  .slot.filled {{ border-style: solid; color: #e6edf3; }}
  .slot.not-here {{ cursor: pointer; }}
  textarea {{ width: 100%; max-width: 640px; height: 200px; background: #0d1117;
             color: #e6edf3; border: 1px solid #30363d; font-family: monospace; }}
  button {{ margin-right: 8px; }}
</style>
</head>
<body>
<h1>SHAL Arena — rack</h1>
<p>Drag an instrument tile into a rack slot to build <code>setup.yaml</code>.</p>

<div class="shelf" id="shelf">
{tiles_html}
</div>

<div class="rack" id="rack">
  <div class="slot" data-slot="0"></div>
  <div class="slot" data-slot="1"></div>
  <div class="slot" data-slot="2"></div>
  <div class="slot" data-slot="3"></div>
  <div class="slot not-here" data-slot="4" id="not-here">My instrument is not here</div>
</div>

<h2>setup.yaml</h2>
<textarea id="yaml-out" readonly></textarea>
<div>
  <button id="download-btn" type="button">Download</button>
  <button id="copy-btn" type="button">Copy</button>
</div>

<script>
const COMPAT_BY_CASE = {compat_json};
{_JS_BUILD_SETUP_YAML}

const slots = Array.from(document.querySelectorAll('.slot[data-slot]')).filter(
  s => s.id !== 'not-here');
let placed = [];

function refresh() {{
  const yamlText = buildSetupYaml(placed, COMPAT_BY_CASE);
  document.getElementById('yaml-out').value = yamlText;
}}

document.querySelectorAll('.tile[draggable="true"]').forEach(tile => {{
  tile.addEventListener('dragstart', ev => {{
    ev.dataTransfer.setData('text/plain', tile.dataset.case);
  }});
}});

slots.forEach((slot, i) => {{
  slot.addEventListener('dragover', ev => ev.preventDefault());
  slot.addEventListener('drop', ev => {{
    ev.preventDefault();
    const caseName = ev.dataTransfer.getData('text/plain');
    if (!caseName) return;
    placed[i] = caseName;
    slot.textContent = caseName;
    slot.classList.add('filled');
    refresh();
  }});
}});

document.getElementById('not-here').addEventListener('click', () => {{
  window.open('{not_here_url}', '_blank', 'noopener');
}});

document.getElementById('download-btn').addEventListener('click', () => {{
  const blob = new Blob([document.getElementById('yaml-out').value],
                        {{type: 'text/yaml'}});
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'setup.yaml';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}});

document.getElementById('copy-btn').addEventListener('click', () => {{
  navigator.clipboard.writeText(document.getElementById('yaml-out').value);
}});
</script>
</body>
</html>
"""
