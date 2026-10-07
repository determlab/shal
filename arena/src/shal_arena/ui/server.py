"""The WATCH/PLAY server (issues #406, #407): stdlib `http.server` only,
bound to ``127.0.0.1`` -- never ``0.0.0.0`` -- so this is reachable from this
machine alone. Serves at most one run at a time: the one `--run <id>` named
at startup, or -- issue #407 -- none yet, bound once a `POST /api/play/start`
creates one. Reads only that run's own files (`ui.data.run_payload` is the
one reader), never any other run under the same state dir.

issue #407 (CTO design review on the mock, 2026-10-07): the POST routes
below are routes and controls only -- every one calls the SAME runner.py
function the CLI calls (`ui.play`), never a second game logic. The page's
own visuals (what GET `/` renders when no run is bound, and the Play
controls' final look) follow #447 once its own mockup is approved; this
round ships the write routes a page can call once that lands.
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..errors import ArenaError
from ..store import DEFAULT_STATE_DIR
from . import play
from .data import run_payload
from .page import render_watch_page

HOST = "127.0.0.1"


def _error_body(e: ArenaError) -> bytes:
    return json.dumps({"ok": False, "error": e.to_dict()}).encode("utf-8")


def _make_handler(run_id: str | None, state_dir: str | Path,
                  drivers: dict[str, dict[str, Any]] | None) -> type[BaseHTTPRequestHandler]:
    # issue #407: a plain mutable container, not a `nonlocal` rebind inside
    # the handler class -- `POST /api/play/start` is the one route that
    # BINDS a run id where there was none; every other route reads it.
    current = {"run_id": run_id}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:  # quiet by default
            pass

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, status: int, doc: dict[str, Any]) -> None:
            self._send(status, json.dumps(doc).encode("utf-8"),
                      "application/json; charset=utf-8")

        def _payload_or_error(self) -> dict[str, Any] | None:
            try:
                payload = run_payload(current["run_id"], state_dir=state_dir)
                payload["drivers"] = drivers or {}
                payload["controls"] = play.instrument_controls(payload["instruments"])
                return payload
            except ArenaError as e:
                self._send(404, _error_body(e), "application/json; charset=utf-8")
                return None

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b"{}"
            doc = json.loads(raw.decode("utf-8")) if raw else {}
            return doc if isinstance(doc, dict) else {}

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
            if self.path == "/" or self.path == "":
                if current["run_id"] is None:
                    # issue #407: no run yet -- the task-picker's own data
                    # (Frame 1 of the approved mock). The page that renders
                    # this follows #447; this is the route it will call.
                    self._send_json(200, {"ok": True, "tasks": play.list_play_tasks()})
                    return
                payload = self._payload_or_error()
                if payload is None:
                    return
                html = render_watch_page(current["run_id"], payload)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if current["run_id"] is not None and self.path == f"/api/run/{current['run_id']}":
                payload = self._payload_or_error()
                if payload is None:
                    return
                self._send(200, json.dumps(payload).encode("utf-8"),
                          "application/json; charset=utf-8")
                return
            self._send(404, b'{"ok": false, "error": "not found"}',
                      "application/json; charset=utf-8")

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
            try:
                body = self._read_json_body()
                if self.path == "/api/play/start":
                    if current["run_id"] is not None:
                        raise ArenaError(
                            "play: a run is already open on this page",
                            fix="finish (Answer) or restart the server before starting another")
                    result = play.start(body.get("task", ""), state_dir=state_dir,
                                        seed=body.get("seed"))
                    current["run_id"] = result["run_id"]
                    self._send_json(200, result)
                    return
                if current["run_id"] is None:
                    raise ArenaError("play: no run is open yet",
                                     fix="POST /api/play/start first, with a task name")
                run_id = current["run_id"]
                if self.path == "/api/play/measure":
                    address = body.get("address", "")
                    case_name = _case_for(run_id, address, state_dir)
                    self._send_json(200, play.measure(run_id, address, case_name,
                                                      state_dir=state_dir))
                    return
                if self.path == "/api/play/drive":
                    self._send_json(200, play.drive(run_id, body.get("address", ""),
                                                     float(body.get("volts", 0.0)),
                                                     state_dir=state_dir))
                    return
                if self.path == "/api/play/switch":
                    address = body.get("address", "")
                    case_name = _case_for(run_id, address, state_dir)
                    self._send_json(200, play.switch(run_id, address, case_name,
                                                      bool(body.get("on")),
                                                      state_dir=state_dir))
                    return
                if self.path == "/api/play/answer":
                    self._send_json(200, play.answer(run_id, body.get("value", ""),
                                                      state_dir=state_dir))
                    return
                self._send(404, b'{"ok": false, "error": "not found"}',
                          "application/json; charset=utf-8")
            except ArenaError as e:
                self._send_json(400, {"ok": False, "error": e.to_dict()})

    return Handler


def _case_for(run_id: str, address: str, state_dir: str | Path) -> str:
    """The instrument's own `case:`, read from its run -- Play never asks
    the caller to name a case; it looks it up (condition 1: nothing
    hardcoded, including on this, the server side)."""
    payload = run_payload(run_id, state_dir=state_dir)
    inst = next((i for i in payload["instruments"] if str(i["address"]) == str(address)), None)
    if inst is None:
        known = ", ".join(str(i["address"]) for i in payload["instruments"])
        raise ArenaError(f"play: no instrument at address {address!r} on run {run_id!r}",
                         fix=f"use one of this run's addresses: {known}")
    return inst["case"]


def serve(run_id: str | None = None, *, state_dir: str | Path = DEFAULT_STATE_DIR, port: int = 0,
         open_browser: bool = True,
         drivers: dict[str, dict[str, Any]] | None = None) -> ThreadingHTTPServer:
    """Bind on `HOST` and the given `port` (0 picks a free one) and return
    the live server -- the caller runs `serve_forever()` (or, in a test,
    polls it directly and shuts it down itself). With a `run_id`: fails
    fast, before binding anything, if it does not exist (same named-fix
    error every other command raises for an unknown run) -- WATCH mode,
    unchanged from #406. With none: PLAY mode (#407), binds with no run
    open; `POST /api/play/start` opens one."""
    if run_id is not None:
        run_payload(run_id, state_dir=state_dir)  # ArenaError, unknown run -- fail before bind
    handler = _make_handler(run_id, state_dir, drivers)
    httpd = ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{httpd.server_address[1]}/"
    if run_id is not None:
        print(f"shal-arena ui: watching {run_id} at {url}")
    else:
        print(f"shal-arena ui: play at {url}")
    if open_browser:
        webbrowser.open(url)
    return httpd
