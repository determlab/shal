"""A minimal in-process Ecovacs cloud portal, implemented from
docs/deebot-cloud-transport.md (DN20-CLOUD) + docs/deebot-protocol.md
(DN20-PROTO). Serves the 3-step auth chain, GetDeviceList (+ the appsvr
fallback) and iot/devmanager.do, routing commands into a small behavioral
robot model. Accepts any login. Stdlib only.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

DEVICE = {"did": "did-bot1", "name": "E0001-bot1", "nick": "bot1",
          "deviceName": "DEEBOT N20", "sn": "E0001",
          "class": "p1jij8", "resource": "atag", "company": "eco-ng"}


class RobotModel:
    """DN20-PROTO state machine, bench power-on defaults (docs §6)."""

    def __init__(self) -> None:
        self.battery = 87
        self.state = "idle"
        self.docked = True

    def handle(self, cmd: str, data: dict) -> tuple[int, dict]:
        if cmd == "getBattery":
            return 0, {"value": self.battery, "isLow": int(self.battery < 15)}
        if cmd == "getCleanInfo_V2":
            return 0, {"trigger": "app", "state": self.state}
        if cmd == "clean_V2":
            act = data.get("act")
            if act == "start":
                self.state, self.docked = "clean", False
            elif act == "pause":
                self.state = "pause"
            elif act == "resume":
                self.state = "clean"
            elif act == "stop":
                self.state = "idle"
            else:
                return 1, {}
            return 0, {}
        if cmd == "charge":
            if data.get("act") != "go":
                return 1, {}
            if self.docked:
                return 30007, {}
            self.state, self.docked = "goCharging", True
            return 0, {}
        if cmd == "playSound":
            return 0, {}
        return 1, {}


class _Handler(BaseHTTPRequestHandler):
    portal: "FakePortal"

    def log_message(self, *a):  # quiet
        pass

    def _send(self, obj: dict) -> None:
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if "/v1/private/" in path and path.endswith("/user/login"):
            self._send({"code": "0000", "msg": "ok",
                        "data": {"uid": "u-bench", "accessToken": "at-bench-1"}})
        elif path.endswith("/v1/global/auth/getAuthCode"):
            self._send({"code": "0000", "msg": "ok",
                        "data": {"authCode": "ac-bench-1"}})
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if path == "/api/users/user.do":
            todo = body.get("todo")
            if todo == "loginByItToken":
                self._send({"result": "ok", "userId": "u-bench",
                            "token": "tk-bench-1"})
            elif todo == "GetDeviceList":
                self._send({"result": "ok", "todo": "result",
                            "devices": [DEVICE]})
            else:
                self._send({"result": "fail", "error": f"unknown todo {todo!r}"})
        elif path == "/api/appsvr/app.do":
            self._send({"ret": "ok", "devices": [DEVICE]})
        elif path == "/api/iot/devmanager.do":
            if body.get("toId") != DEVICE["did"]:
                self._send({"ret": "fail", "errno": "4200"})
                return
            data = ((body.get("payload") or {}).get("body") or {}).get("data") or {}
            code, out = self.portal.robot.handle(body.get("cmdName"), data)
            resp_body: dict = {"code": code,
                               "msg": "ok" if code in (0, 30007) else "fail"}
            if out:
                resp_body["data"] = out
            self._send({"ret": "ok",
                        "resp": {"header": {"pri": "1"}, "body": resp_body}})
        else:
            self.send_error(404)


class FakePortal:
    """Threaded portal on an ephemeral 127.0.0.1 port. Use as context manager
    or call close(). `robot` is the behavioral model — set/inspect state."""

    def __init__(self) -> None:
        self.robot = RobotModel()
        handler = type("_Bound", (_Handler,), {"portal": self})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def __enter__(self) -> "FakePortal":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
