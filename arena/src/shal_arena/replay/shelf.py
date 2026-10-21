"""The demo's device shelf (issue #506): twelve instruments in four groups
(power supply, scope, DMM, signal generator / DAQ). A device can go in the
rack only if it has a driver; one without is grey, cannot be dragged, and
says what to do next.

The registry below is the one source of truth. "Has a driver" is not a flag
typed here: a device is `present` only when the packaged reference driver
file it names (`ui.play.REFERENCE_DRIVERS`, the four drivers Play already
ships) exists, and its line count is read from that file. A device with no
such file is `missing` with 0 lines.

Everything here is read-only (`side_effect: none`). The page
(`render_shelf_page`) is only a way for a person to see what `shelf_listing`
and `rack_check` already return, which is also what
`shal-arena shelf --json` prints for an agent."""
from __future__ import annotations

import html
import json
from typing import Any

from ..errors import ArenaError
from ..ui.play import REFERENCE_DRIVERS
from .rack import DRIVER_REQUEST_URL

GROUPS: tuple[tuple[str, str], ...] = (
    ("psu", "Power supply"),
    ("scope", "Scope"),
    ("dmm", "DMM"),
    ("siggen-daq", "Signal generator / DAQ"),
)

#: (id, group, model, connection, case). `case` names the reference driver in
#: `REFERENCE_DRIVERS`; None = no driver written yet.
_DEVICES: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("bench-psu1", "psu", "Bench PSU 1 (scpi-psu)", "SCPI over LAN", "scpi-psu"),
    ("rigol-dp832", "psu", "Rigol DP832", "SCPI over USB or LAN", None),
    ("keysight-e36312a", "psu", "Keysight E36312A", "SCPI over USB or LAN", None),
    ("rigol-ds1054z", "scope", "Rigol DS1054Z", "SCPI over LAN", None),
    ("keysight-dsox1204g", "scope", "Keysight DSOX1204G", "SCPI over USB or LAN", None),
    ("siglent-sds1104xe", "scope", "Siglent SDS1104X-E", "SCPI over LAN", None),
    ("bench-dmm1", "dmm", "Bench DMM 1 (dmm)", "SCPI over LAN", "dmm"),
    ("keithley-dmm6500", "dmm", "Keithley DMM6500", "SCPI over LAN", None),
    ("keysight-34461a", "dmm", "Keysight 34461A", "SCPI over LAN", None),
    ("bench-relay1", "siggen-daq", "Bench relay module 1 (relay-modbus)", "Modbus", "relay-modbus"),
    ("bench-temp1", "siggen-daq", "Bench temperature sensor 1 (sht31)", "I2C", "sht31"),
    ("keysight-33500b", "siggen-daq", "Keysight 33500B signal generator", "SCPI over LAN", None),
)

_WRITE_DRIVER_FIX = (
    "write its driver: run `shal docs` (the guide to add a device) and start from "
    "`shal docs --example tmp102`, check it with `shal check <vendor,part> --json`; or ask "
    f"for it at {DRIVER_REQUEST_URL}")


class ShelfError(ArenaError):
    """A shelf refusal (a grey device dragged, an unknown device id). Exit 2:
    a usage mistake, nothing was changed."""

    exit_code = 2


def _device(row: tuple[str, str, str, str, str | None]) -> dict[str, Any]:
    device_id, group, model, connection, case = row
    path = REFERENCE_DRIVERS.get(case) if case else None
    present = path is not None and path.is_file()
    lines = len(path.read_text(encoding="utf-8").splitlines()) if present else 0
    return {"id": device_id, "group": group, "model": model, "connection": connection,
            "driver": "present" if present else "missing", "driver_lines": lines,
            "case": case if present else None,
            "next_step": None if present else _WRITE_DRIVER_FIX}


def shelf_devices() -> list[dict[str, Any]]:
    """The twelve shelf devices, grouped in `GROUPS` order."""
    return [_device(r) for g, _ in GROUPS for r in _DEVICES if r[1] == g]


def shelf_listing() -> dict[str, Any]:
    """What `shal-arena shelf --json` prints: every device with
    ``driver: present|missing``, readable without the UI."""
    devices = shelf_devices()
    present = sum(d["driver"] == "present" for d in devices)
    return {"ok": True, "side_effect": "none", "groups": [{"id": g, "name": n} for g, n in GROUPS],
            "present": present, "missing": len(devices) - present, "devices": devices}


def _lookup(device_id: str) -> dict[str, Any]:
    devices = {d["id"]: d for d in shelf_devices()}
    if device_id not in devices:
        raise ShelfError(f"shelf: no device {device_id!r}",
                         fix=f"use one of: {', '.join(devices)}")
    return devices[device_id]


def drag(device_id: str) -> dict[str, Any]:
    """Take a device off the shelf for the rack. A grey device is refused,
    with the next step in the message."""
    device = _lookup(device_id)
    if device["driver"] != "present":
        raise ShelfError(f"shelf: {device['model']} has no driver yet, so it cannot be "
                         "dragged to the rack", fix=device["next_step"])
    return device


def rack_check(device_ids: list[str]) -> dict[str, Any]:
    """Check a rack: which of these devices have no driver. ``ok`` is true
    only when every one has; `missing` names each other one and its next step."""
    if not device_ids:
        raise ShelfError("shelf: the rack is empty", fix="name at least one device id, "
                         "e.g. `shal-arena shelf --check bench-psu1 --json`")
    devices = [_lookup(i) for i in device_ids]
    missing = [{"id": d["id"], "model": d["model"], "next_step": d["next_step"]}
               for d in devices if d["driver"] != "present"]
    return {"ok": not missing, "side_effect": "none", "rack": list(device_ids),
            "missing": missing}


def _card_html(d: dict[str, Any]) -> str:
    model, ident = html.escape(d["model"]), html.escape(d["id"])
    facts = (f'<div class="meta">{html.escape(d["connection"])}</div>'
             f'<div class="meta">driver: {d["driver"]}, {d["driver_lines"]} lines</div>')
    if d["driver"] == "present":
        return (f'<div class="card" draggable="true" data-id="{ident}">'
                f'<div class="model">{model}</div>{facts}</div>')
    return (f'<div class="card grey" data-id="{ident}"><div class="model">{model}</div>{facts}'
            f'<a class="write-driver" href="{html.escape(DRIVER_REQUEST_URL)}" '
            f'target="_blank" rel="noopener">Write driver</a></div>')


def render_shelf_page() -> str:
    """The shelf page: four groups of cards, a rack to drag the ones with a
    driver into, a status line that carries a refusal, and Check rack."""
    devices = shelf_devices()
    groups_html = "\n".join(
        f'<h2>{html.escape(name)}</h2><div class="group">'
        + "".join(_card_html(d) for d in devices if d["group"] == g) + "</div>"
        for g, name in GROUPS)
    data = json.dumps({d["id"]: d for d in devices}).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>SHAL Arena - device shelf</title>
<style>
  body {{ font-family: sans-serif; margin: 0; padding: 24px; background: #0b0f14; color: #e6edf3; }}
  .group, .rack {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 8px 0 20px; }}
  .card {{ border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px;
          background: #161b22; min-width: 200px; cursor: grab; }}
  .card.grey {{ opacity: 0.55; cursor: not-allowed; }}
  .model {{ font-weight: 600; }}
  .meta {{ font-size: 12px; color: #8b949e; }}
  .write-driver {{ display: inline-block; margin-top: 6px; font-size: 12px; }}
  .rack {{ min-height: 64px; border: 2px dashed #30363d; border-radius: 8px; padding: 8px; }}
  #status {{ min-height: 1.4em; margin: 8px 0; }}
</style>
</head>
<body>
<h1>SHAL Arena - device shelf</h1>
<p>Drag a device to the rack. Only a device with a driver can go.</p>
{groups_html}
<h2>Rack</h2>
<div class="rack" id="rack"></div>
<div id="status" role="status"></div>
<button id="check-btn" type="button">Check rack</button>
<script>
const DEVICES = {data};
const WRITE_DRIVER = {json.dumps(_WRITE_DRIVER_FIX)};
const placed = [];
const status = document.getElementById('status');
function refuse(d) {{
  status.textContent = d.model + ' has no driver yet, so it cannot be dragged to the rack. '
    + 'Next step: ' + WRITE_DRIVER;
}}
document.querySelectorAll('.card').forEach(card => {{
  const d = DEVICES[card.dataset.id];
  if (d.driver === 'present') {{
    card.addEventListener('dragstart', ev => ev.dataTransfer.setData('text/plain', d.id));
  }} else {{
    card.addEventListener('click', () => refuse(d));
  }}
}});
const rack = document.getElementById('rack');
rack.addEventListener('dragover', ev => ev.preventDefault());
rack.addEventListener('drop', ev => {{
  ev.preventDefault();
  const d = DEVICES[ev.dataTransfer.getData('text/plain')];
  if (!d) return;
  if (d.driver !== 'present') {{ refuse(d); return; }}
  placed.push(d.id);
  const el = document.createElement('div');
  el.className = 'card';
  el.textContent = d.model;
  rack.appendChild(el);
}});
document.getElementById('check-btn').addEventListener('click', () => {{
  // The rack only ever holds driven devices (a grey card is refused on drop);
  // the missing-driver list for a given rack is `rack_check` / `--check`.
  status.textContent = !placed.length ? 'The rack is empty.'
    : 'Rack ok: every device has a driver.';
}});
</script>
</body>
</html>
"""
