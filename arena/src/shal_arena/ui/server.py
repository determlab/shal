"""The WATCH server (issue #406): stdlib `http.server` only, bound to
``127.0.0.1`` -- never ``0.0.0.0`` -- so this is reachable from this machine
alone. Serves exactly one run: the one `--run <id>` named at startup. Reads
only that run's own files (`ui.data.run_payload` is the one reader), never
any other run under the same state dir, and never writes anything (the
Scope: "read: ... binds a local port, opens a browser").
"""
from __future__ import annotations

import json
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from ..errors import ArenaError
from ..store import DEFAULT_STATE_DIR
from .data import run_payload
from .page import render_watch_page

HOST = "127.0.0.1"


def _make_handler(run_id: str, state_dir: str | Path) -> type[BaseHTTPRequestHandler]:
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

        def _payload_or_error(self) -> dict[str, Any] | None:
            try:
                return run_payload(run_id, state_dir=state_dir)
            except ArenaError as e:
                self._send(404, json.dumps(e.to_dict()).encode("utf-8"),
                          "application/json; charset=utf-8")
                return None

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
            if self.path == "/" or self.path == "":
                payload = self._payload_or_error()
                if payload is None:
                    return
                html = render_watch_page(run_id, payload)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if self.path == f"/api/run/{run_id}":
                payload = self._payload_or_error()
                if payload is None:
                    return
                self._send(200, json.dumps(payload).encode("utf-8"),
                          "application/json; charset=utf-8")
                return
            self._send(404, b'{"ok": false, "error": "not found"}',
                      "application/json; charset=utf-8")

    return Handler


def serve(run_id: str, *, state_dir: str | Path = DEFAULT_STATE_DIR, port: int = 0,
         open_browser: bool = True) -> ThreadingHTTPServer:
    """Bind on `HOST` and the given `port` (0 picks a free one) and return
    the live server -- the caller runs `serve_forever()` (or, in a test,
    polls it directly and shuts it down itself). Fails fast, before binding
    anything, if `run_id` does not exist (same named-fix error every other
    command raises for an unknown run)."""
    run_payload(run_id, state_dir=state_dir)  # ArenaError, unknown run -- fail before bind
    handler = _make_handler(run_id, state_dir)
    httpd = ThreadingHTTPServer((HOST, port), handler)
    url = f"http://{HOST}:{httpd.server_address[1]}/"
    print(f"shal-arena ui: watching {run_id} at {url}")
    if open_browser:
        webbrowser.open(url)
    return httpd
