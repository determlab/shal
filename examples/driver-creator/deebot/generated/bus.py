"""SHAL cloud bus for ECOVACS DEEBOT robots (DN20-CLOUD rev 1.1).

A leaf bus that reaches a DEEBOT through the ECOVACS cloud and provides a
``MessageTransport`` to its child robot drivers. It performs the documented
3-step auth chain lazily in ``activate()``, enumerates devices with
``GetDeviceList``, and maps each ``{"cmd","data"}`` device command onto the
``iot/devmanager.do`` relay envelope (DN20-CLOUD §8), returning the portal's
``{"ret","resp"}`` mapping unchanged so the device driver sees the exact
DN20-PROTO envelope.

This bus talks HTTPS directly (stdlib only), so it has no upstream SHAL hop:
``kind = None``. Buses may be imperative (SDK §9).
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

import shal
from shal import Driver, HopError, LoadError, MessageTransport, Transport, register
from shal.log import bus_logger, current_txn

# --- DN20-CLOUD §1 application identity constants ---------------------------
CLIENT_KEY = "1520391301804"
CLIENT_SECRET = "6c319b2a5cd3e66e39159c2e28f2fce9"
AUTH_CLIENT_KEY = "1520391491841"
AUTH_CLIENT_SECRET = "77ef58ce3afbe337da74aa8c5ab963a9"
REALM = "ecouser.net"
META = {"lang": "EN", "appCode": "global_e", "appVersion": "1.6.3",
        "channel": "google_play", "deviceType": "1"}
USER_AGENT = "Dalvik/2.1.0 (Linux; U; Android 5.1.1; A5010 Build/LMY48Z)"

# DN20-CLOUD §2 continent derivation.
_CONTINENT = {}
for _cc in "at be bg ch cy cz de dk ee es fi fr gb gr hr hu ie is it li lt lu lv mc mt nl no pl pt ro se si sk sm uk".split():
    _CONTINENT[_cc] = "eu"
for _cc in "ca mx us".split():
    _CONTINENT[_cc] = "na"
for _cc in "hk id il in jp kr my ph sa sg th tw vn".split():
    _CONTINENT[_cc] = "as"


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _sign(params: dict, key: str, secret: str) -> str:
    """DN20-CLOUD §3: MD5(key + concat(sorted name=value) + secret)."""
    body = "".join(f"{k}={params[k]}" for k in sorted(params))
    return _md5(key + body + secret)


@register
class EcovacsCloudBus(Driver, Transport, MessageTransport):
    compatible = "ecovacs,cloud-n20"
    kind = None  # leaf bus: talks HTTPS itself, no upstream SHAL transport

    def __init__(self, node) -> None:
        Transport.__init__(self, node)  # sets self.host, self.lock, self._active
        self.log = bus_logger("ecovacs_cloud", node.path)

        # The bus's node address is the lowercase two-letter country code (§1).
        cc = str(node.address or "").strip().lower()
        if len(cc) != 2 or not cc.isalpha():
            raise LoadError(
                f"{node.path}: ecovacs,cloud-n20 address must be a two-letter "
                f"country code (e.g. 'us', 'de'), got {node.address!r}")
        self.cc = cc

        cfg = (node.spec.get("config") or {})
        self._user = cfg.get("user") or os.environ.get("ECOVACS_EMAIL")
        self._password = cfg.get("password") or os.environ.get("ECOVACS_PASSWORD")
        # portal_url override (config key, env fallback) — points every endpoint
        # at a single origin (test bench / self-hosted portal), DN20-CLOUD §9.
        self._portal_url = (cfg.get("portal_url")
                            or os.environ.get("ECOVACS_PORTAL_URL"))
        # continent override (§2): config or env, else derived from cc.
        self._continent = (cfg.get("continent")
                           or os.environ.get("ECOVACS_CONTINENT")
                           or _CONTINENT.get(cc, "ww"))

        # Session state — established in activate(), dropped in close().
        self._session = None

    # --- child address grammar ---------------------------------------------

    def validate_address(self, addr) -> None:
        if not isinstance(addr, str) or not addr.strip():
            raise LoadError(
                f"{self.host.path}: child robot address must be a non-empty "
                f"device id / name / nick / sn string, got {addr!r}")

    def kinds(self):
        return (MessageTransport,)

    # --- base URLs (production regional, or single portal_url override) ------

    def _bases(self):
        if self._portal_url:
            p = self._portal_url.rstrip("/")
            return p, p, p + "/api"
        cc = self.cc
        cont = self._continent
        return (f"https://gl-{cc}-api.ecovacs.com",
                f"https://gl-{cc}-openapi.ecovacs.com",
                f"https://portal-{cont}.ecouser.net/api")

    # --- HTTP helpers -------------------------------------------------------

    def _http(self, method: str, url: str, *, body=None):
        """Perform one HTTP request; return parsed JSON. Errors -> HopError.

        Honest delivered=: a connection/refusal before send is "no"; anything
        after the request goes out (timeout, HTTP error, bad body) is "unknown".
        Error text names the URL PATH only, never the query string (§9).
        """
        path = urllib.parse.urlsplit(url).path
        data = None
        headers = {"User-Agent": USER_AGENT}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            # The server received and answered with an error status -> unknown.
            raise HopError(f"cloud HTTP {e.code} at {path}", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="unknown") from e
        except urllib.error.URLError as e:
            # Could not connect / DNS / refused: request never left -> no.
            raise HopError(f"cloud connect failed at {path}", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="no") from e
        except OSError as e:
            raise HopError(f"cloud I/O error at {path}", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="unknown") from e
        try:
            return json.loads(raw)
        except ValueError as e:
            raise HopError(f"cloud sent non-JSON at {path}", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="unknown") from e

    def _get(self, base: str, path: str, params: dict):
        url = base + path + "?" + urllib.parse.urlencode(params)
        return self._http("GET", url, body=None)

    def _post(self, base: str, path: str, body: dict):
        return self._http("POST", base + path, body=body)

    # --- lifecycle ----------------------------------------------------------

    def is_active(self) -> bool:
        # Cheap LOCAL check (§9): we hold a portal session.
        return self._session is not None

    def activate(self) -> None:
        if self._user is None or self._password is None:
            raise HopError(
                "ecovacs credentials missing (config user/password or "
                "ECOVACS_EMAIL/ECOVACS_PASSWORD)", path=self.host.path,
                hop="ecovacs-cloud", txn=current_txn.get(), delivered="no")

        t0 = time.time()
        main, openapi, portal = self._bases()

        # Per-session application identity (§1).
        device_id = secrets.token_hex(16)          # 32-char lowercase hex
        resource = device_id[:8]
        country = self.cc

        # --- Step 1: account login (main API) -------------------------------
        pw_hash = _md5(self._password)
        ts1 = str(int(time.time() * 1000))
        signed1 = {
            "country": country, "deviceId": device_id, "lang": META["lang"],
            "appCode": META["appCode"], "appVersion": META["appVersion"],
            "channel": META["channel"], "deviceType": META["deviceType"],
            "account": self._user, "password": pw_hash,
            "requestId": _md5(str(time.time())), "authTimespan": ts1,
            "authTimeZone": "GMT-8",
        }
        login_path = (f"/v1/private/{country}/{META['lang']}/{device_id}/"
                      f"{META['appCode']}/{META['appVersion']}/"
                      f"{META['channel']}/{META['deviceType']}/user/login")
        q1 = dict(signed1)
        q1["authSign"] = _sign(signed1, CLIENT_KEY, CLIENT_SECRET)
        q1["authAppkey"] = CLIENT_KEY
        r1 = self._get(main, login_path, q1)
        if str(r1.get("code")) != "0000":
            # Login refused (bad creds / rate limit): nothing reached any robot.
            raise HopError("ecovacs account login refused", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="no")
        uid = r1["data"]["uid"]
        access_token = r1["data"]["accessToken"]

        # --- Step 2: auth code (open API) -----------------------------------
        ts2 = str(int(time.time() * 1000))
        signed2 = {"uid": uid, "accessToken": access_token,
                   "bizType": "ECOVACS_IOT", "deviceId": device_id,
                   "authTimespan": ts2, "openId": "global"}
        q2 = dict(signed2)
        q2["authSign"] = _sign(signed2, AUTH_CLIENT_KEY, AUTH_CLIENT_SECRET)
        q2["authAppkey"] = AUTH_CLIENT_KEY
        r2 = self._get(openapi, "/v1/global/auth/getAuthCode", q2)
        if str(r2.get("code")) != "0000":
            raise HopError("ecovacs auth-code request refused",
                           path=self.host.path, hop="ecovacs-cloud",
                           txn=current_txn.get(), delivered="no")
        auth_code = r2["data"]["authCode"]

        # --- Step 3: portal session (loginByItToken) ------------------------
        r3 = self._post(portal, "/users/user.do", {
            "edition": "ECOGLOBLE", "userId": uid, "token": auth_code,
            "realm": REALM, "resource": resource, "org": "ECOWW",
            "last": "", "country": country.upper(), "todo": "loginByItToken"})
        if r3.get("result") != "ok":
            raise HopError("ecovacs portal login refused", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="no")
        portal_uid = r3["userId"]
        portal_token = r3["token"]
        auth = {"with": "users", "userid": portal_uid, "realm": REALM,
                "token": portal_token, "resource": resource}

        self._session = {
            "portal": portal, "resource": resource,
            "portal_uid": portal_uid, "auth": auth,
            "devices": self._get_device_list(portal, portal_uid, auth),
        }
        self.log.info("connected", event="connect",
                      duration_ms=round((time.time() - t0) * 1000))

    def _get_device_list(self, portal: str, portal_uid: str, auth: dict):
        # DN20-CLOUD §7: GetDeviceList, with the GetGlobalDeviceList fallback.
        r = self._post(portal, "/users/user.do",
                       {"userid": portal_uid, "auth": auth,
                        "todo": "GetDeviceList"})
        devices = r.get("devices") or []
        if not devices:
            r2 = self._post(portal, "/appsvr/app.do",
                            {"userid": portal_uid, "auth": auth,
                             "todo": "GetGlobalDeviceList"})
            devices = r2.get("devices") or []
        return devices

    def close(self) -> None:
        # Drop connection AND session so reconnect re-runs the auth chain (§9).
        self._session = None
        self._active = False

    # --- device resolution --------------------------------------------------

    def _resolve(self, addr: str) -> dict:
        """Match a child address against did / name / nick / deviceName / sn."""
        for d in self._session["devices"]:
            if any(str(d.get(k)) == addr
                   for k in ("did", "name", "nick", "deviceName", "sn")):
                return d
        raise HopError(f"no ecovacs robot matches {addr!r}", path=self.host.path,
                       hop="ecovacs-cloud", txn=current_txn.get(),
                       delivered="no")

    # --- MessageTransport: command relay (DN20-CLOUD §8) --------------------

    def exchange(self, addr, msg):
        with self.lock:
            self.ensure_ready()
            sess = self._session
            dev = self._resolve(addr)
            did = dev["did"]
            klass = dev.get("class", "")
            cmd = msg["cmd"]
            data = msg.get("data")

            payload = {"header": {"pri": "1", "ts": time.time(),
                                  "tzm": 480, "ver": "0.0.50"}}
            # When data is null/absent, omit payload.body entirely (§8).
            if data is not None:
                payload["body"] = {"data": data}

            envelope = {
                "cmdName": cmd, "payload": payload, "payloadType": "j",
                "td": "q", "toId": did,
                "toRes": dev.get("resource", "") or "",
                "toType": klass or "",
                "auth": sess["auth"],
            }
            query = urllib.parse.urlencode({
                "mid": klass, "did": did, "td": "q",
                "u": sess["portal_uid"], "cv": "1.67.3", "t": "a", "av": "1.3.1"})
            url = sess["portal"] + "/iot/devmanager.do?" + query
            self.log.debug("relay command", event="exchange", cmd=cmd)
            reply = self._http("POST", url, body=envelope)

            if reply.get("ret") != "ok":
                # Portal failed / robot offline: may or may not have reached the
                # robot -> delivery unknown (§8). Never blind-retry actuations.
                raise HopError(
                    f"ecovacs portal relay failed (errno={reply.get('errno')})",
                    path=self.host.path, hop="ecovacs-cloud",
                    txn=current_txn.get(), delivered="unknown")
            # Return the {"ret","resp"} mapping unchanged: the device driver
            # sees the exact DN20-PROTO envelope.
            return reply

    # --- authoring surface --------------------------------------------------

    @classmethod
    def authoring_meta(cls) -> dict:
        return {
            "address_schema": {
                "type": "string", "pattern": r"^[a-z]{2}$",
                "description": "account country code (lowercase, two letters)",
                "examples": ["us", "de"],
            },
            "child_address_schema": {
                "type": "string",
                "description": "robot did / name / nick / sn",
                "examples": ["did-bot1"],
            },
            "config_schema": {
                "type": "object",
                "properties": {
                    "user": {"type": "string"},
                    "password": {"type": "string"},
                    "portal_url": {"type": "string"},
                    "continent": {"type": "string",
                                  "enum": ["eu", "na", "as", "ww"]},
                },
                "additionalProperties": False,
            },
        }
