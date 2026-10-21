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

issue #407 (page round, after #484 merged): in PLAY mode `GET /` is the Play
page (`page.render_play_page`), never JSON. The task list it renders is also
`GET /api/play/tasks`, and the open run's answer choices are
`GET /api/play/choices` -- the JSON an agent reads instead of the page.
"""
from __future__ import annotations

import contextlib
import hmac
import io
import json
import math
import secrets
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import runner as _runner
from .. import stages
from ..errors import ArenaError
from ..loader import load_task, once_per_path
from ..remote import MAX_PAYLOAD_BYTES, RemoteSessions
from ..runner import has_any_measurement
from ..store import DEFAULT_STATE_DIR, RunStore
from . import play
from .data import run_payload
from .page import render_play_page, render_watch_page

HOST = "127.0.0.1"

#: CTO review on #407 round 2, must-fix 6: every POST route, in WATCH mode.
_PLAY_ROUTES = ("/api/play/start", "/api/play/measure", "/api/play/drive",
               "/api/play/switch", "/api/play/answer", "/api/demo/start")

#: issue #486 (`shal-arena serve`): the routes, and the keys each one takes.
#: The Play routes are the same ones above (same Host/Origin/Content-Type
#: checks, same `play.*` functions); every call but `start` carries the run's
#: token. A key not listed is a 400 -- no route takes code, a file path or a
#: module name to run, and a body that tries is refused, not ignored.
_RUN = {"run_id", "token", "note"}
_REMOTE_KEYS: dict[str, set[str]] = {
    "/api/play/start": {"task", "seed", "note"},
    "/api/play/measure": _RUN | {"address", "at"},
    "/api/play/drive": _RUN | {"address", "volts"},
    "/api/play/switch": _RUN | {"address", "on"},
    "/api/play/answer": _RUN | {"value"},
    "/api/remote/begin": _RUN | {"action", "address", "at"},
    "/api/remote/exchange": _RUN | {"session", "address", "message"},
    "/api/remote/txn": _RUN | {"session", "address", "ops"},
    "/api/remote/finish": _RUN | {"session", "op", "args", "ok", "reading", "unit", "failed",
                                  "cause"},
}

#: `answer`'s score as a client sees it: the fields that say nothing about the
#: seed or the realized fault (the rest stay in the server's own score file).
_PUBLIC_SCORE = ("task_id", "duration_s", "turns", "gate_stops", "schema_version",
                 "game_version", "record_sha256")

_WILDCARD_HOSTS = ("0.0.0.0", "")


class _MethodNotAllowed(ArenaError):
    """must-fix 6: WATCH mode (a fixed `--run <id>`) never writes -- every
    POST route 405s, not 400, so a caller can tell "this page is read-only"
    apart from any of Play's own refusals."""


class _Forbidden(ArenaError):
    """must-fix 5a: a cross-origin page (or a DNS-rebinding attacker) can
    make a browser send a POST here without a CORS preflight, as long as
    the request looks like a plain form submit -- wrong Content-Type, or a
    Host/Origin that is not this server. 403, not 400: the request itself
    is not trusted, independent of what it asks for."""


class _TooLarge(ArenaError):
    """issue #486: a `serve` request body over `MAX_PAYLOAD_BYTES` -- 413."""


def _public_answer(result: dict[str, Any]) -> dict[str, Any]:
    """issue #486: what `answer` returns to a client of `serve` -- the local
    `answer`'s own fields minus the ones that name the seed or the realized
    fault (`fault_id`, and the score's `seed`, `fault_type` and the counts
    that follow from it). The server's own score file keeps all of them."""
    doc = _strip_paths(result)
    doc.pop("fault_id", None)
    doc["score"] = {k: v for k, v in doc["score"].items() if k in _PUBLIC_SCORE}
    return doc


def _error_body(e: ArenaError) -> bytes:
    return json.dumps({"ok": False, "error": e.to_dict()}).encode("utf-8")


#: CTO review on #407 round 3, must-fix 1: `answer`'s own record also
#: carries `task_path`/`card_path` (`store.answer`'s `record`, `store.py`),
#: both real filesystem paths on this machine -- round 2 only caught
#: `path` (the task list) and `sim_log`. Named here, in one place, so a
#: future field gets a deliberate decision instead of silently passing
#: through.
_PATH_FIELDS = ("path", "sim_log", "task_path", "card_path")


def _strip_paths(doc: dict[str, Any]) -> dict[str, Any]:
    """must-fix 5c: nothing this server sends over HTTP carries a real
    filesystem path -- a browser never needs one to play. Both this run's
    own files (for the CLI, or a script on this machine) and anything a
    browser needs are in `run_payload`/the Play routes' own result dicts
    some other way (an id, a case name, a reading)."""
    return {k: v for k, v in doc.items() if k not in _PATH_FIELDS}


def _make_handler(run_id: str | None, state_dir: str | Path,
                  drivers: dict[str, dict[str, Any]] | None, *, remote: bool = False,
                  bind_host: str = HOST) -> type[BaseHTTPRequestHandler]:
    # issue #407: a plain mutable container, not a `nonlocal` rebind inside
    # the handler class -- `POST /api/play/start` is the one route that
    # BINDS a run id where there was none; every other route reads it.
    #
    # `play_mode` is fixed at serve() time (CTO review on #407 round 2,
    # must-fix 6): WATCH mode's `current["run_id"]` is never None, so
    # `current["run_id"] is None` cannot tell the 2 modes apart on its own.
    # `last_run_id`: the run this PLAY server closed most recently -- the
    # page reads its result card from `/api/run/<id>` after Answer unbinds
    # it. Only ever a run THIS server started; never any other run.
    current = {"run_id": run_id, "last_run_id": None}
    play_mode = run_id is None
    # CTO review on #407 round 3 nit: `current["run_id"]` was check-then-set
    # with no lock, on a `ThreadingHTTPServer` -- 2 concurrent
    # `POST /api/play/start` calls (a phone double-tap) both see no run
    # open and both start one, orphaning every run but the last to bind.
    # One lock around the whole check-and-bind closes the window.
    start_lock = threading.Lock()
    # issue #520: the demo's start cards. `rack` is the fullest bench any
    # card has started; choosing another card never clears or shrinks it.
    # The lock also guards the stdout capture `_demo_from` does.
    demo = {"stage": None, "rack": []}
    demo_lock = threading.Lock()
    # issue #486 (`shal-arena serve`): many runs at once, each behind its own
    # token (in memory only: a restarted server forgets them), and the open
    # remote-bus sessions.
    tokens: dict[str, str] = {}
    sessions = RemoteSessions(state_dir)
    routes = tuple(_REMOTE_KEYS) if remote else _PLAY_ROUTES

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

        def _payload_or_error(self, run_id: str) -> dict[str, Any] | None:
            try:
                payload = run_payload(run_id, state_dir=state_dir)
                payload["drivers"] = drivers or {}
                payload["controls"] = play.instrument_controls(payload["instruments"])
                # issue #407: the Answer grid's on/off state, from the one
                # rule `runner.answer` itself applies -- never a second one.
                payload["has_measurement"] = has_any_measurement(run_id, state_dir=state_dir)
                return payload
            except ArenaError as e:
                self._send(404, _error_body(e), "application/json; charset=utf-8")
                return None

        def _check_host(self) -> str:
            """must-fix 5a (round 3 nit): shared by GET and POST alike --
            without a Host check on GET too, a DNS-rebound foreign page
            could still read `GET /`/`/api/run/<id>` even though no POST
            route was ever reachable from it."""
            port = self.server.server_address[1]
            want_host = f"{bind_host}:{port}"
            host = self.headers.get("Host", "")
            if bind_host in _WILDCARD_HOSTS and host.rpartition(":")[2] == str(port):
                want_host = host  # an explicit --host 0.0.0.0: any name for this port
            if host != want_host:
                raise _Forbidden(f"play: unexpected Host header {host!r}",
                                 fix=f"send requests to {want_host} only")
            return want_host

        def _same_origin_check(self) -> None:
            """must-fix 5a. `Host` must name this server (`_check_host`); a
            present `Origin` must too -- absent (a non-browser client: the
            CLI's own future use, curl, an agent's HTTP client) is allowed,
            since there is no browser trust model to enforce there."""
            content_type = self.headers.get("Content-Type", "")
            if content_type.split(";")[0].strip().lower() != "application/json":
                raise _Forbidden(
                    f"play: Content-Type must be application/json, got {content_type!r}",
                    fix='send the request with header Content-Type: application/json')
            want_host = self._check_host()
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{want_host}":
                raise _Forbidden(f"play: unexpected Origin header {origin!r}",
                                 fix=f"this server only accepts requests from http://{want_host}")

        def _read_json_body(self) -> dict[str, Any]:
            """must-fix 3: malformed JSON, or a body that is not a JSON
            object, is this route's own 400 -- never an uncaught exception
            (a dropped connection with a traceback) and never silently
            treated as `{}`."""
            length = int(self.headers.get("Content-Length", "0") or "0")
            if remote and length > MAX_PAYLOAD_BYTES:
                # drain what a keep-alive connection still owes (bounded), so
                # the 413 reaches a client that is still sending
                left = min(length, 16 * MAX_PAYLOAD_BYTES)
                while left > 0:
                    chunk = self.rfile.read(min(left, 65536))
                    if not chunk:
                        break
                    left -= len(chunk)
                if length > 16 * MAX_PAYLOAD_BYTES:
                    self.close_connection = True
                raise _TooLarge(
                    f"remote: the request body is {length} bytes, over the "
                    f"{MAX_PAYLOAD_BYTES}-byte cap",
                    fix=f"send at most {MAX_PAYLOAD_BYTES} bytes per request")
            raw = self.rfile.read(length) if length else b""
            if not raw.strip():
                return {}
            try:
                doc = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as e:
                raise ArenaError(f"play: the request body is not valid JSON: {e}",
                                 fix="send a JSON object body") from e
            if not isinstance(doc, dict):
                raise ArenaError("play: the request body must be a JSON object",
                                 fix='send a JSON object body, e.g. {"address": "psu0"}')
            return doc

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
            try:
                self._check_host()
            except _Forbidden as e:
                self._send_json(403, {"ok": False, "error": e.to_dict()})
                return
            if remote:  # `serve` is headless: POST routes only
                self._send_json(404, {"ok": False, "error": {
                    "type": "NotFound", "message": "not found",
                    "fix": f"POST one of {', '.join(routes)}"}})
                return
            if self.path == "/" or self.path == "":
                if play_mode:
                    # issue #407: the Play page -- the task picker with no
                    # run open, the controls once one is. Styled HTML, never
                    # JSON; the same data is at /api/play/tasks.
                    html = render_play_page(play.list_play_tasks(), current["run_id"])
                    self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                    return
                payload = self._payload_or_error(current["run_id"])
                if payload is None:
                    return
                html = render_watch_page(current["run_id"], payload)
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
                return
            if play_mode and self.path == "/api/play/tasks":
                tasks = [_strip_paths(t) for t in play.list_play_tasks()]
                self._send_json(200, {"ok": True, "tasks": tasks})
                return
            if play_mode and self.path == "/api/demo/stages":
                self._send_json(200, {"ok": True, "stages": list(stages.STAGES),
                                      "default": stages.DEFAULT_STAGE, **demo})
                return
            if play_mode and self.path == "/api/play/choices":
                if current["run_id"] is None:
                    self._send_json(400, {"ok": False, "error": ArenaError(
                        "play: no run is open yet",
                        fix="POST /api/play/start first, with a task name").to_dict()})
                    return
                self._send_json(200, {"ok": True, "run_id": current["run_id"],
                                      "values": _answer_values(current["run_id"], state_dir)})
                return
            served = {current["run_id"], current["last_run_id"]} - {None}
            run_path = self.path.removeprefix("/api/run/")
            if self.path.startswith("/api/run/") and run_path in served:
                payload = self._payload_or_error(run_path)
                if payload is None:
                    return
                self._send(200, json.dumps(payload).encode("utf-8"),
                          "application/json; charset=utf-8")
                return
            # must never echo the requested path: it may name a run_id this
            # server was never given to watch (test_ui_api.py's own
            # structural guarantee -- "not found" says nothing else).
            self._send_json(404, {"ok": False,
                                  "error": {"type": "NotFound", "message": "not found",
                                           "fix": "GET / or /api/run/<run_id> instead"}})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's own name
            try:
                # protocol_version is HTTP/1.1 (keep-alive): the body MUST
                # be drained off the socket before any response is sent, on
                # every path through this handler, or a later request on
                # the same connection reads a stale, unparsed body instead
                # of its own headers -- read it first, unconditionally,
                # even for a request this handler ends up rejecting before
                # ever looking at the body's contents.
                body = self._read_json_body()
                if self.path not in routes:
                    self._send_json(404, {
                        "ok": False, "error": {
                            "type": "NotFound", "message": "not found",
                            "fix": f"POST one of {', '.join(routes)}"}})
                    return
                self._same_origin_check()
                if not play_mode:
                    raise _MethodNotAllowed(
                        f"play: {self.path} is read-only in WATCH mode",
                        fix="start a server with no --run (PLAY mode) to use this route")
                self._dispatch(body)
            except _Forbidden as e:
                self._send_json(403, {"ok": False, "error": e.to_dict()})
            except _MethodNotAllowed as e:
                self._send_json(405, {"ok": False, "error": e.to_dict()})
            except _TooLarge as e:
                self._send_json(413, {"ok": False, "error": e.to_dict()})
            except ArenaError as e:
                self._send_json(400, {"ok": False, "error": e.to_dict()})
            except Exception as e:  # noqa: BLE001 - a bug here must still answer in shape
                self._send_json(500, {"ok": False, "error": {
                    "type": type(e).__name__, "message": str(e),
                    "fix": "this is a shal-arena bug, not a request problem -- file an issue"}})

        def _authorize(self, body: dict[str, Any]) -> str:
            """issue #486: the run this call is for, if it carries that run's
            token. A missing token, a wrong one, another run's, and an unknown
            run all answer the same 403 -- it never says which."""
            run_id, token = body.get("run_id"), body.get("token")
            expected = tokens.get(run_id) if isinstance(run_id, str) else None
            if (expected is None or not isinstance(token, str)
                    or not hmac.compare_digest(token.encode(), expected.encode())):
                raise _Forbidden(
                    "remote: this call has no valid token for this run",
                    fix="pass the token that `run` returned (--token TOKEN, or \"token\" "
                        "in the body) together with that run's run_id")
            return run_id

        def _logged(self, run_id: str, argv: list[str], note: str | None, fn) -> dict[str, Any]:
            """issue #486: run one command and write its `<run>.cli.jsonl`
            line on THIS side, the way a local `shal-arena` call does -- the
            client keeps no log of its own. `argv` never holds the token."""
            from ..cli import _log_cli_call
            try:
                doc = fn()
            except ArenaError as e:
                _log_cli_call(str(state_dir), run_id, argv, e.exit_code,
                              {"ok": False, "error": e.to_dict()}, "", note)
                raise
            _log_cli_call(str(state_dir), run_id, argv, 0 if doc.get("ok", True) else 1,
                          doc, "", note)
            return doc

        def _dispatch_remote(self, body: dict[str, Any]) -> None:
            """issue #486: every route of a `shal-arena serve` server -- the
            Play routes (the same `play.*` / `runner.*` functions) and the
            remote-bus ones. Nothing here takes code, a path or a module."""
            path = self.path
            unknown = sorted(set(body) - _REMOTE_KEYS[path])
            if unknown:
                raise ArenaError(
                    f"remote: {path} does not take {unknown}; this server runs no code "
                    "and opens no path",
                    fix=f"send only: {', '.join(sorted(_REMOTE_KEYS[path]))}")
            note = body.get("note")
            if note is not None:
                from ..cli import NOTE_MAX_CHARS, _note_too_long_error
                if not isinstance(note, str):
                    raise ArenaError("remote: 'note' must be a string",
                                     fix="send 'note' as text, or leave it out")
                if len(note) > NOTE_MAX_CHARS:
                    raise _note_too_long_error(note)
            if path == "/api/play/start":
                task = _require_str(body, "task", example='{"task": "rail-3v3"}')
                seed = _require_optional_int(body, "seed")
                result = _strip_paths(play.start(task, state_dir=state_dir, seed=seed))
                run_id = result["run_id"]
                tokens[run_id] = secrets.token_urlsafe(16)
                argv = ["run", task] + ([] if seed is None else ["--seed", str(seed)])
                self._logged(run_id, argv, note, lambda: result)
                self._send_json(200, {**result, "token": tokens[run_id]})
                return
            run_id = self._authorize(body)
            if path == "/api/remote/exchange":
                self._send_json(200, sessions.exchange(
                    run_id, body.get("session"), body.get("address"), body.get("message")))
                return
            if path == "/api/remote/txn":
                self._send_json(200, sessions.txn(
                    run_id, body.get("session"), body.get("address"), body.get("ops")))
                return
            if path == "/api/remote/begin":
                self._send_json(200, sessions.begin(
                    run_id, _require_str(body, "action", example='{"action": "measure"}'),
                    _require_str(body, "address", example='{"address": "dmm0"}'),
                    _optional_str(body, "at", example='{"address": "dmm0", "at": "tp_5v"}')))
                return
            if path == "/api/remote/finish":
                sess = sessions.get(run_id, body.get("session"))
                argv = [sess.action, run_id, sess.address]
                if sess.action == "call":
                    argv += [str(body.get("op")), *map(str, body.get("args") or [])]
                elif sess.point is not None:
                    argv += ["--at", sess.point]
                doc = self._logged(run_id, argv, note, lambda: sessions.finish(
                    run_id, body.get("session"), body))
                self._send_json(200, doc)
                return
            if path == "/api/play/answer":
                value = _require_str(body, "value", example='{"value": "ok"}')
                _check_answer_value(run_id, value, state_dir)
                # the local `answer`, not Play's: no measurement is a
                # `disqualified` answer here too, never a refusal
                self._send_json(200, self._logged(
                    run_id, ["answer", run_id, value], note,
                    lambda: _public_answer(_runner.answer(run_id, value, state_dir=state_dir))))
                return
            address = _require_str(body, "address", example='{"address": "psu0"}')
            if path == "/api/play/drive":
                volts = _require_finite_number(body, "volts")
                doc = self._logged(run_id, ["drive", run_id, address, str(volts)], note,
                                   lambda: _strip_paths(play.drive(
                                       run_id, address, volts, state_dir=state_dir)))
            elif path == "/api/play/switch":
                on = _require_bool(body, "on")
                doc = self._logged(run_id, ["call", run_id, address, "set_relay", "0",
                                            "true" if on else "false"], note,
                                   lambda: _strip_paths(play.switch(
                                       run_id, address, _case_for(run_id, address, state_dir),
                                       on, state_dir=state_dir)))
            else:  # /api/play/measure
                at = _optional_str(body, "at", example='{"address": "dmm0", "at": "tp_5v"}')
                doc = self._logged(run_id, ["measure", run_id, address]
                                   + ([] if at is None else ["--at", at]), note,
                                   lambda: _strip_paths(play.measure(
                                       run_id, address, _case_for(run_id, address, state_dir),
                                       at=at, state_dir=state_dir)))
            self._send_json(200, doc)

        def _dispatch(self, body: dict[str, Any]) -> None:
            if remote:
                self._dispatch_remote(body)
                return
            if self.path == "/api/demo/start":
                stage = _require_str(body, "stage", example='{"stage": "racked"}')
                with demo_lock:
                    doc = _demo_from(stage)
                    instruments = doc["steps"][0]["result"].get("instruments")
                    demo["stage"] = stage
                    if len(instruments or []) > len(demo["rack"]):  # never shrinks
                        demo["rack"] = instruments
                    self._send_json(200, {**doc, **demo})
                return
            if self.path == "/api/play/start":
                with start_lock:
                    if current["run_id"] is not None:
                        raise ArenaError(
                            "play: a run is already open on this page",
                            fix="finish (Answer) before starting another")
                    task = _require_str(body, "task", example='{"task": "rail-3v3"}')
                    seed = _require_optional_int(body, "seed")
                    result = play.start(task, state_dir=state_dir, seed=seed)
                    current["run_id"] = result["run_id"]
                self._send_json(200, _strip_paths(result))
                return
            if current["run_id"] is None:
                raise ArenaError("play: no run is open yet",
                                 fix="POST /api/play/start first, with a task name")
            run_id = current["run_id"]
            if self.path == "/api/play/measure":
                address = _require_str(body, "address", example='{"address": "dmm0"}')
                case_name = _case_for(run_id, address, state_dir)
                at = _optional_str(body, "at", example='{"address": "dmm0", "at": "tp_5v"}')
                self._send_json(200, _strip_paths(play.measure(
                    run_id, address, case_name, at=at, state_dir=state_dir)))
                return
            if self.path == "/api/play/drive":
                address = _require_str(body, "address", example='{"address": "psu0"}')
                volts = _require_finite_number(body, "volts")
                self._send_json(200, _strip_paths(
                    play.drive(run_id, address, volts, state_dir=state_dir)))
                return
            if self.path == "/api/play/switch":
                address = _require_str(body, "address", example='{"address": "relay0"}')
                on = _require_bool(body, "on")
                self._send_json(200, _strip_paths(play.switch(
                    run_id, address, _case_for(run_id, address, state_dir),
                    on, state_dir=state_dir)))
                return
            if self.path == "/api/play/answer":
                value = _require_str(body, "value", example='{"value": "ok"}')
                _check_answer_value(run_id, value, state_dir)
                result = play.answer(run_id, value, state_dir=state_dir)
                # must-fix 7: unbind once the run closes, so the next
                # POST /api/play/start is not stuck behind a finished run.
                current["run_id"] = None
                current["last_run_id"] = run_id
                self._send_json(200, _strip_paths(result))
                return

    return Handler


def _demo_from(stage: str) -> dict[str, Any]:
    """`shal-arena demo --from STAGE --json`, run through the same
    `stages.run_from` the CLI calls; its one JSON document is what the CLI
    would print. An unknown stage raises `UnknownStage` (a 400 here). The
    caller holds `demo_lock`: stdout is process-wide."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        # issue #507: `--trace`, so the "Under the hood" button shows this very document
        stages.run_from(stage, pause=0, json_mode=True, trace=True)
    return json.loads(out.getvalue())


def _require_str(body: dict[str, Any], key: str, *, example: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise ArenaError(f"play: {key!r} must be a non-empty string",
                         fix=f"send a body like {example}")
    return value


def _optional_str(body: dict[str, Any], key: str, *, example: str) -> str | None:
    """issue #498: `at` on measure -- omitted (or null) for a one-point
    instrument, else a non-empty string the runner checks against the
    instrument's own points."""
    if key not in body or body[key] is None:
        return None
    return _require_str(body, key, example=example)


def _require_bool(body: dict[str, Any], key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise ArenaError(f"play: {key!r} must be a JSON boolean (true/false)",
                         fix=f"send {key!r} as true or false, e.g. " + '{"' + key + '": true}')
    return value


def _require_finite_number(body: dict[str, Any], key: str) -> float:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArenaError(f"play: {key!r} must be a number",
                         fix=f'send a body like {{"address": "psu0", "{key}": 5.0}}')
    value = float(value)
    if not math.isfinite(value):
        raise ArenaError(f"play: {key!r} must be a finite number, got {value}",
                         fix="pass a finite voltage, e.g. 5.0")
    return value


def _require_optional_int(body: dict[str, Any], key: str) -> int | None:
    if key not in body or body[key] is None:
        return None
    value = body[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArenaError(f"play: {key!r} must be an integer, or omitted",
                         fix=f"send {key!r} as a JSON integer, or leave it out")
    return value


def _answer_values(run_id: str, state_dir: str | Path) -> list[str]:
    """The open run's answer choices -- the task yaml's own
    `question.answer.values`, the same list `POST /api/play/start` already
    returned (#461: the visible possible faults, never a fixed list)."""
    state = RunStore(state_dir).load(run_id)
    return list(load_task(state.task_path).task.question.answer.values)


def _check_answer_value(run_id: str, value: str, state_dir: str | Path) -> None:
    """Nit (CTO review on #407 round 2): `{"value": 12345}` already fails
    `_require_str`; this covers the other shape -- a syntactically fine
    string that is not one of the task's own enum values -- with the same
    400-and-list-them shape, before `runner.answer` ever closes the run."""
    state = RunStore(state_dir).load(run_id)
    answer_spec = load_task(state.task_path).task.question.answer
    if answer_spec.kind != "enum":
        return
    values = list(answer_spec.values)
    if value not in values:
        raise ArenaError(f"play: {value!r} is not a valid answer for run {run_id!r}",
                         fix=f"use one of: {', '.join(values)}")


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
         drivers: dict[str, dict[str, Any]] | None = None,
         host: str = HOST, remote: bool = False) -> ThreadingHTTPServer:
    """Bind on `HOST` and the given `port` (0 picks a free one) and return
    the live server -- the caller runs `serve_forever()` (or, in a test,
    polls it directly and shuts it down itself). With a `run_id`: fails
    fast, before binding anything, if it does not exist (same named-fix
    error every other command raises for an unknown run) -- WATCH mode,
    unchanged from #406. With none: PLAY mode (#407), binds with no run
    open; `POST /api/play/start` opens one.

    issue #486: ``remote=True`` is `shal-arena serve` -- headless, many runs,
    a token per run, plus the remote-bus routes; ``host`` is where it binds
    (``127.0.0.1`` unless the caller was told explicitly, in `cli.py`). It
    prints nothing: the `serve` command prints its one JSON line itself."""
    if run_id is not None:
        run_payload(run_id, state_dir=state_dir)  # ArenaError, unknown run -- fail before bind
    handler = _make_handler(run_id, state_dir, drivers, remote=remote, bind_host=host)
    httpd = ThreadingHTTPServer((host, port), handler)

    # issue #407 round 2, must-fix 1: `runner._import_driver_file` re-execs
    # the file on every call, making a brand-new class object each time --
    # fine for one CLI call, but Play's own reference drivers are imported
    # over and over for the life of this server, and a second import makes
    # the registry see 2 distinct candidates for the same `compatible`
    # ("claimed by 2 drivers"). Cache the import by resolved path for as
    # long as THIS server is alive -- the same `loader.once_per_path` tool
    # `bench.py`'s `_driver_imported_once` already uses for the same
    # reason, scoped the same way: restored on `server_close()`.
    #
    # round 3 nit: only PLAY mode (`run_id is None`) ever imports a driver
    # -- WATCH mode never calls `measure`/`drive`/`switch`, so swapping
    # this process-wide function there patches nothing real and only risks
    # 2 servers closing out of order leaving the wrong one installed. PLAY
    # mode itself still serves one run at a time, so this remains scoped
    # to "the one server that could possibly need it."
    real_server_close = httpd.server_close
    if run_id is None:
        original_import = _runner._import_driver_file
        _runner._import_driver_file = once_per_path(original_import)

        def _server_close() -> None:
            _runner._import_driver_file = original_import
            real_server_close()

        httpd.server_close = _server_close

    url = f"http://{HOST}:{httpd.server_address[1]}/"
    if not remote:
        print(f"shal-arena ui: watching {run_id} at {url}" if run_id is not None
              else f"shal-arena ui: play at {url}")
    if open_browser:
        webbrowser.open(url)
    return httpd
