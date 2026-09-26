"""shal,http request envelope (issue #104; ADK spec §3.7 S1).

Two message shapes, chosen by the message: plain (POST the mapping, reply = the
parsed body — unchanged) and the envelope {method, path, query, headers, json}
-> {status, headers, json | text}. Credentials live on the bus node's
`config.headers` and never reach driver-visible data. A non-2xx is a HopError
naming the status, sent exactly once. Tested against a real 127.0.0.1 server
started here and against `shal,sim-msg`, which answers the same envelopes.
"""
import http.server
import json
import logging
import textwrap
import threading
import urllib.parse

import pytest

import shal
from shal.buses.sim_msg import msg_sim_model

TOKEN = "tok-5ecret-104"      # what ${SHAL104_TOKEN} resolves to
API_KEY = "key-5ecret-104"    # what ${SHAL104_KEY} resolves to
QUERY_SECRET = "q-5ecret-104"  # a credential-shaped query value


# ---- a local HTTP server that records every request ------------------------------

class _Server:
    """ThreadingHTTPServer on 127.0.0.1:0. `routes` maps a path to
    (status, content_type, body bytes); every request is recorded."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.routes: dict[str, tuple[int, str, bytes]] = {}
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def _serve(self):
                length = int(self.headers.get("Content-Length") or 0)
                split = urllib.parse.urlsplit(self.path)
                outer.requests.append({
                    "method": self.command, "path": split.path,
                    "query": urllib.parse.parse_qs(split.query),
                    "headers": dict(self.headers.items()),
                    "body": self.rfile.read(length) if length else b"",
                })
                if split.path.endswith("/redirect"):
                    self.send_response(302)
                    self.send_header("Location", "/api/landing")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status, ctype, body = outer.routes.get(
                    split.path, (200, "application/json", b'{"ok": true}'))
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = _serve

            def log_message(self, *args):  # keep test output clean
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def server():
    s = _Server()
    yield s
    s.close()


@pytest.fixture
def secrets_env(monkeypatch):
    monkeypatch.setenv("SHAL104_TOKEN", TOKEN)
    monkeypatch.setenv("SHAL104_KEY", API_KEY)


def _topology(tmp_path, port: int, *, headers: bool = True):
    cfg = ("""
            config:
              headers:
                Authorization: "Bearer ${SHAL104_TOKEN}"
                X-Api-Key: "${SHAL104_KEY}"
    """ if headers else "")
    p = tmp_path / "rest.yaml"
    p.write_text(textwrap.dedent(f"""
        shal_version: 1
        root:
          api:
            id: api
            driver: shal,http
            address: http://127.0.0.1:{port}
            insecure: true{cfg.rstrip()}
            children:
              cal: {{id: cal, driver: "test,rest-reader", address: api}}
    """), encoding="utf-8")
    return p


# ---- a software driver: reads a REST resource with one envelope --------------------

@shal.register
class RestReader(shal.Driver):
    compatible = "test,rest-reader"
    kind = shal.MessageTransport
    llm_ready = True

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[dict] = []     # every envelope this driver handed the bus
        self.replies: list = []        # every reply the bus handed back

    def _send(self, msg: dict):
        self.sent.append(json.loads(json.dumps(msg)))
        reply = self.bus.exchange(self.addr, msg)
        self.replies.append(reply)
        return reply

    @shal.idempotent
    @shal.op("List events in a window.", side_effect="none")
    def list_events(self, page_token: str | None = None) -> list:
        reply = self._send({
            "method": "GET", "path": "v1/events",
            "query": {"timeMin": "2026-08-31T00:00:00Z", "singleEvents": True,
                      "pageToken": page_token},
            # a driver trying to set its own Authorization loses to the bus's
            "headers": {"Accept": "application/json", "authorization": "Bearer forged"},
        })
        return reply["json"]["items"]

    @shal.idempotent
    @shal.op("Read one resource by path.", side_effect="none")
    def get_path(self, path: str) -> dict:
        return self._send({"method": "GET", "path": path})

    @shal.idempotent
    @shal.op("Ping in the plain shape.", side_effect="none")
    def ping(self) -> dict:
        return self._send({"cmd": "ping"})


# ---- local HTTP server ------------------------------------------------------------

def test_plain_shape_unchanged_posts_the_mapping(tmp_path, server):
    server.routes["/api"] = (200, "application/json", b'{"pong": 1}')
    with shal.load(_topology(tmp_path, server.port, headers=False)) as hal:
        assert hal.get_device("cal").ping() == {"pong": 1}  # the parsed body, as before
    (req,) = server.requests
    assert req["method"] == "POST" and req["path"] == "/api"
    assert json.loads(req["body"]) == {"cmd": "ping"}
    assert req["headers"]["Content-Type"] == "application/json"


def test_envelope_get_with_query_returns_status_headers_json(tmp_path, server, secrets_env):
    server.routes["/api/v1/events"] = (200, "application/json",
                                       b'{"items": [{"id": "e1"}]}')
    with shal.load(_topology(tmp_path, server.port)) as hal:
        cal = hal.get_device("cal")
        assert cal.list_events() == [{"id": "e1"}]
        reply = cal.replies[-1]
    assert reply["status"] == 200
    assert reply["headers"]["Content-Type"] == "application/json"
    assert reply["json"] == {"items": [{"id": "e1"}]}
    (req,) = server.requests
    assert req["method"] == "GET" and req["path"] == "/api/v1/events"
    assert req["query"] == {"timeMin": ["2026-08-31T00:00:00Z"],
                            "singleEvents": ["true"]}   # pageToken=None dropped
    assert req["body"] == b""


def test_envelope_reply_non_json_body_is_text(tmp_path, server):
    server.routes["/api/readme"] = (200, "text/plain", b"hello")
    with shal.load(_topology(tmp_path, server.port, headers=False)) as hal:
        reply = hal.get_device("cal").get_path("readme")
    assert reply["status"] == 200 and reply["text"] == "hello" and "json" not in reply


def test_configured_headers_sent_on_every_request_and_never_driver_visible(
        tmp_path, server, secrets_env):
    server.routes["/api/v1/events"] = (200, "application/json", b'{"items": []}')
    with shal.load(_topology(tmp_path, server.port)) as hal:
        cal = hal.get_device("cal")
        cal.list_events()
        cal.list_events(page_token="p2")
        cal.ping()
        visible = json.dumps({"sent": cal.sent, "replies": cal.replies,
                              "driver_spec": cal.node.spec,
                              "bus_spec": hal.get_node("api").spec})
    assert len(server.requests) == 3
    for req in server.requests:   # plain AND envelope; the bus's value wins
        assert req["headers"]["Authorization"] == f"Bearer {TOKEN}"
        assert req["headers"]["X-Api-Key"] == API_KEY
    # nothing a driver sends, receives, or can read off a node holds the secret
    assert TOKEN not in visible and API_KEY not in visible
    assert "${SHAL104_TOKEN}" in visible  # the topology holds only the reference


def test_configured_headers_not_forwarded_across_a_redirect(tmp_path, server, secrets_env):
    with shal.load(_topology(tmp_path, server.port)) as hal:
        reply = hal.get_device("cal").get_path("/redirect")
    assert reply["status"] == 200
    first, landing = server.requests
    assert first["path"] == "/api/redirect" and landing["path"] == "/api/landing"
    assert first["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert "Authorization" not in landing["headers"]
    assert "X-Api-Key" not in landing["headers"]


def test_missing_header_env_var_is_a_load_error_naming_it(tmp_path, server, monkeypatch):
    monkeypatch.setenv("SHAL104_KEY", API_KEY)
    monkeypatch.delenv("SHAL104_TOKEN", raising=False)
    with pytest.raises(shal.LoadError, match="SHAL104_TOKEN") as ei:
        shal.load(_topology(tmp_path, server.port))
    assert API_KEY not in str(ei.value)
    assert server.requests == []


@pytest.mark.parametrize("status", [404, 500])
def test_non_2xx_is_a_hop_error_with_status_sent_exactly_once(
        tmp_path, server, secrets_env, status):
    server.routes["/api/v1/events"] = (status, "application/json", b'{"error": "x"}')
    with shal.load(_topology(tmp_path, server.port)) as hal:
        with pytest.raises(shal.HopError, match=f"HTTP {status}") as ei:
            hal.get_device("cal").list_events()   # @idempotent — still never retried
    assert len(server.requests) == 1
    msg = str(ei.value)
    assert TOKEN not in msg and "timeMin" not in msg  # query redacted from the error


def test_json_body_on_get_is_a_load_error_and_sends_nothing(tmp_path, server):
    with shal.load(_topology(tmp_path, server.port, headers=False)) as hal:
        bus = hal.get_node("api").driver
        with pytest.raises(shal.LoadError, match="json.*GET"):
            bus.exchange("api", {"method": "GET", "path": "x", "json": {"a": 1}})
    assert server.requests == []


def test_envelope_with_json_defaults_to_post(tmp_path, server):
    with shal.load(_topology(tmp_path, server.port, headers=False)) as hal:
        reply = hal.get_node("api").driver.exchange("api", {"path": "orders",
                                                           "json": {"item": "x"}})
    assert reply["status"] == 200
    (req,) = server.requests
    assert req["method"] == "POST" and json.loads(req["body"]) == {"item": "x"}


def test_logs_carry_path_and_status_never_query_or_headers(
        tmp_path, server, secrets_env, caplog):
    server.routes["/api/v1/events"] = (404, "application/json", b"{}")
    caplog.set_level(logging.DEBUG, logger="shal")
    with shal.load(_topology(tmp_path, server.port)) as hal:
        bus = hal.get_node("api").driver
        bus.exchange("api", {"path": "v1/items", "query": {"key": QUERY_SECRET},
                             "headers": {"X-Trace": QUERY_SECRET}})
        with pytest.raises(shal.HopError):
            bus.exchange("api", {"path": "v1/events", "query": {"key": QUERY_SECRET}})
    dumped = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    for secret in (TOKEN, API_KEY, QUERY_SECRET, "key="):
        assert secret not in dumped
    assert "GET api/v1/items -> 200" in dumped
    assert "GET api/v1/events -> 404" in dumped


# ---- shal,sim-msg answers the same envelopes --------------------------------------

@msg_sim_model("test,rest-reader")
class _CalendarModel:
    def __init__(self) -> None:
        self.seen: list = []

    def handle(self, msg):
        self.seen.append(msg)
        if msg.get("cmd") == "ping":
            return {"pong": 1}                       # plain in, plain out
        if msg["path"] == "v1/events":
            return {"items": [{"id": "sim-e1"}]}     # plain body -> 200 + json
        return {"status": 404, "text": "no such resource"}  # full shape


SIM_YAML = """
    shal_version: 1
    root:
      api:
        id: api
        driver: shal,sim-msg
        address: sim0
        children:
          cal: {id: cal, driver: "test,rest-reader", address: api}
"""


def _sim(tmp_path):
    p = tmp_path / "sim.yaml"
    p.write_text(textwrap.dedent(SIM_YAML), encoding="utf-8")
    return p


def test_sim_msg_answers_the_envelope(tmp_path):
    with shal.load(_sim(tmp_path)) as hal:
        cal = hal.get_device("cal")
        assert cal.list_events() == [{"id": "sim-e1"}]
        assert cal.replies[-1] == {"status": 200, "headers": {},
                                   "json": {"items": [{"id": "sim-e1"}]}}
        assert cal.ping() == {"pong": 1}           # plain shape unchanged
        seen = hal.get_node("api").driver.model_for("api").seen[0]
    assert seen["method"] == "GET" and seen["path"] == "v1/events"
    assert seen["query"] == {"timeMin": "2026-08-31T00:00:00Z",
                             "singleEvents": "true"}   # None dropped, as on the wire


def test_sim_msg_non_2xx_is_a_hop_error_with_status(tmp_path):
    with shal.load(_sim(tmp_path)) as hal:
        with pytest.raises(shal.HopError, match="HTTP 404"):
            hal.get_device("cal").get_path("missing")
        assert len(hal.get_node("api").driver.model_for("api").seen) == 1  # once


def test_sim_msg_json_body_on_get_is_a_load_error(tmp_path):
    with shal.load(_sim(tmp_path)) as hal:
        bus = hal.get_node("api").driver
        with pytest.raises(shal.LoadError, match="json.*GET"):
            bus.exchange("api", {"method": "GET", "json": {"a": 1}})
        assert bus.model_for("api").seen == []
