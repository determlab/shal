"""Tests for the ECOVACS cloud bus (ecovacs,cloud-n20) against a local fake
portal served over real HTTP. The fake implements DN20-CLOUD §4-§8 and relays
device commands to the DN20-PROTO sim behaviour, so the full stack
driver -> bus -> HTTP -> fake portal -> robot is exercised.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import shal
import bus as busmod  # noqa: E402  (registers the cloud bus)

# A robot record the fake portal will report from GetDeviceList.
ROBOT = {"did": "did-bot1", "name": "Kitchen", "nick": "Bertie",
         "sn": "SN123", "class": "p1jij8", "resource": "atag"}


class _FakePortalHandler(BaseHTTPRequestHandler):
    # Minimal DN20-PROTO robot state machine inside the fake portal.
    robot_state = {"battery": 87, "state": "idle", "docked": True}

    def log_message(self, *a):  # silence
        pass

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if "/user/login" in self.path:  # step 1
            return self._send({"code": "0000", "msg": "ok",
                               "data": {"uid": "u-bench", "accessToken": "tk"}})
        if "/auth/getAuthCode" in self.path:  # step 2
            return self._send({"code": "0000",
                               "data": {"authCode": "ac-bench"}})
        return self._send({"code": "9999"}, 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n) or b"{}")
        path = self.path.split("?", 1)[0]
        todo = req.get("todo")

        if path == "/api/users/user.do" and todo == "loginByItToken":  # step 3
            return self._send({"result": "ok", "userId": "pu-bench",
                               "token": "pt-bench"})
        if path == "/api/users/user.do" and todo == "GetDeviceList":  # §7
            return self._send({"result": "ok", "devices": [ROBOT]})
        if path == "/api/iot/devmanager.do":  # §8 relay
            return self._send(self._relay(req))
        return self._send({"result": "fail"}, 404)

    def _relay(self, req):
        cmd = req.get("cmdName")
        body = (req.get("payload") or {}).get("body") or {}
        data = body.get("data")
        st = _FakePortalHandler.robot_state
        resp_body = self._robot(cmd, data, st)
        return {"ret": "ok",
                "resp": {"header": {"pri": "1"}, "body": resp_body}}

    @staticmethod
    def _robot(cmd, data, st):
        if cmd == "getBattery":
            v = st["battery"]
            return {"code": 0, "msg": "ok",
                    "data": {"value": v, "isLow": 1 if v < 15 else 0}}
        if cmd == "getCleanInfo_V2":
            return {"code": 0, "msg": "ok",
                    "data": {"trigger": "app", "state": st["state"]}}
        if cmd == "clean_V2":
            act = (data or {}).get("act")
            mapping = {"start": ("clean", False), "pause": ("pause", False),
                       "resume": ("clean", False), "stop": ("idle", False)}
            if act in mapping:
                st["state"], st["docked"] = mapping[act]
                return {"code": 0, "msg": "ok"}
            return {"code": 1, "msg": "fail"}
        if cmd == "charge":
            if (data or {}).get("act") != "go":
                return {"code": 1, "msg": "fail"}
            if st["docked"]:
                return {"code": 30007, "msg": "ok"}
            st["state"], st["docked"] = "goCharging", False
            return {"code": 0, "msg": "ok"}
        if cmd == "playSound":
            return {"code": 0, "msg": "ok"}
        return {"code": 1, "msg": "fail"}


@pytest.fixture
def portal():
    _FakePortalHandler.robot_state = {"battery": 87, "state": "idle",
                                      "docked": True}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakePortalHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        srv.shutdown()


@pytest.fixture
def topo(portal, tmp_path, monkeypatch):
    monkeypatch.setenv("ECOVACS_EMAIL", "jane@example.com")
    monkeypatch.setenv("ECOVACS_PASSWORD", "hunter2")
    monkeypatch.setenv("ECOVACS_PORTAL_URL", portal)
    y = tmp_path / "cloud.yaml"
    y.write_text(
        "shal_version: 1\n"
        "root:\n"
        "  cloud:\n"
        "    id: cloud\n"
        "    driver: ecovacs,cloud-n20\n"
        "    address: us\n"
        "    children:\n"
        "      robot:\n"
        "        id: robot\n"
        "        driver: ecovacs,deebot-n20\n"
        "        address: did-bot1\n",
        encoding="utf-8")
    return str(y)


@pytest.fixture
def hal(topo):
    h = shal.load(topo)
    try:
        yield h
    finally:
        h.close()


# The driver must be importable to bind under the cloud bus.
@pytest.fixture(autouse=True)
def _driver():
    import driver  # noqa: F401


def test_full_stack_battery(hal):
    robot = hal.get_device("robot")
    assert robot.get_battery_percent() == 87  # W: relay -> portal -> robot


def test_full_stack_state_machine(hal):
    robot = hal.get_device("robot")
    assert robot.get_clean_state() == "idle"
    robot.start_cleaning()
    assert robot.get_clean_state() == "clean"
    robot.pause()
    assert robot.get_clean_state() == "pause"
    robot.resume()
    assert robot.get_clean_state() == "clean"


def test_dock_already_docked_30007_success(hal):
    robot = hal.get_device("robot")
    robot.dock()  # docked at start -> 30007, must not raise (W4 / DN20-PROTO §2)
    assert robot.get_clean_state() == "idle"


def test_resolve_by_nick(hal):
    # Address could be did/name/nick/sn; the topology used did-bot1, but
    # resolution by nick is also valid. Exercise the bus resolver directly.
    cloud = hal.get_device("cloud")
    # trigger activation by one exchange
    hal.get_device("robot").get_battery_percent()
    dev = cloud._resolve("Bertie")
    assert dev["did"] == "did-bot1"


def test_lazy_connect_and_close(hal):
    cloud = hal.get_device("cloud")
    assert cloud.is_active() is False          # nothing sent yet
    hal.get_device("robot").get_battery_percent()
    assert cloud.is_active() is True           # session established lazily
    cloud.close()
    assert cloud.is_active() is False          # session dropped


def test_missing_credentials_raises_hoperror(portal, tmp_path, monkeypatch):
    import driver  # noqa: F401
    monkeypatch.delenv("ECOVACS_EMAIL", raising=False)
    monkeypatch.delenv("ECOVACS_PASSWORD", raising=False)
    monkeypatch.setenv("ECOVACS_PORTAL_URL", portal)
    y = tmp_path / "nocreds.yaml"
    y.write_text(
        "shal_version: 1\nroot:\n  cloud:\n    id: cloud\n"
        "    driver: ecovacs,cloud-n20\n    address: us\n"
        "    children:\n      robot:\n        id: robot\n"
        "        driver: ecovacs,deebot-n20\n        address: did-bot1\n",
        encoding="utf-8")
    h = shal.load(str(y))
    try:
        with pytest.raises(shal.HopError) as exc:
            h.get_device("robot").get_battery_percent()
        assert exc.value.delivered == "no"
    finally:
        h.close()


def test_bad_country_code_fails_load(tmp_path, monkeypatch):
    import driver  # noqa: F401
    monkeypatch.setenv("ECOVACS_EMAIL", "x@y.z")
    monkeypatch.setenv("ECOVACS_PASSWORD", "p")
    y = tmp_path / "badcc.yaml"
    y.write_text(
        "shal_version: 1\nroot:\n  cloud:\n    id: cloud\n"
        "    driver: ecovacs,cloud-n20\n    address: usa\n",
        encoding="utf-8")
    with pytest.raises(shal.LoadError):
        shal.load(str(y))


def test_signature_vectors():
    # DN20-CLOUD §10 W1-W3: lock the signing math to the published digests.
    assert busmod._md5("hunter2") == "2ab96390c7dbe3439de74d0c9b0b1767"
    signed1 = {
        "country": "us", "deviceId": "3f8e2a14c9d04b6aa1b2c3d4e5f60718",
        "lang": "EN", "appCode": "global_e", "appVersion": "1.6.3",
        "channel": "google_play", "deviceType": "1",
        "account": "jane@example.com",
        "password": "2ab96390c7dbe3439de74d0c9b0b1767",
        "requestId": "9a1de8c0a9b6f1f3e2d4c5b6a7980102",
        "authTimespan": "1718000000000", "authTimeZone": "GMT-8"}
    assert busmod._sign(signed1, busmod.CLIENT_KEY, busmod.CLIENT_SECRET) == \
        "19d8c5e101d5b4c8b51a39e7c4b9fc0f"
    signed2 = {"uid": "20240612abcdef01", "accessToken": "tk-9f8e7d6c",
               "bizType": "ECOVACS_IOT",
               "deviceId": "3f8e2a14c9d04b6aa1b2c3d4e5f60718",
               "authTimespan": "1718000000123", "openId": "global"}
    assert busmod._sign(signed2, busmod.AUTH_CLIENT_KEY,
                        busmod.AUTH_CLIENT_SECRET) == \
        "c5ef28d621d0cdc0be193b8b7e96dc1b"
