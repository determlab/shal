"""ecovacs,cloud — MessageTransport to the Ecovacs cloud portal.

Playground bus, registered explicitly via shal.register (the documented
in-process path; a published package would use the `shal.drivers` entry-point
group instead).

Protocol: the reverse-engineered Ecovacs app API, as implemented by the
open-source deebot-client / sucks projects (verified against deebot-client,
2026-06):

    1. GET  https://gl-{cc}-api.ecovacs.com/v1/private/.../user/login
            -> uid, accessToken            (signed, _CLIENT_KEY/_CLIENT_SECRET)
    2. GET  https://gl-{cc}-openapi.ecovacs.com/v1/global/auth/getAuthCode
            -> authCode                    (signed, _AUTH_CLIENT_KEY/_SECRET)
    3. POST https://portal-{continent}.ecouser.net/api/users/user.do
            todo=loginByItToken            -> portal userId + token
    4. POST .../api/users/user.do  todo=GetDeviceList   -> devices
    5. POST .../api/iot/devmanager.do                   -> JSON command ("td": "q")

Credentials come from the environment (ECOVACS_EMAIL / ECOVACS_PASSWORD) —
secrets never live in topology files (DESIGN V2 'Security'), and resolved
values never appear in logs or error messages.

Node address = the account's lowercase two-letter country code ("us", "de", …).
exchange(addr, msg): addr identifies the robot (did / name / nick / serial);
msg = {"cmd": <cmdName>, "data": <args dict or None>}. Returns the portal
response mapping ({"ret": "ok", "resp": {...}}) — contents stay opaque to the
bus; the deebot driver owns their meaning (kind owns the shape, not the data).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Mapping

from shal import Driver, HopError, HopTimeout, LoadError, MessageTransport, Transport, register
from shal.log import bus_logger, current_txn
from shal.node import Node

logger = logging.getLogger("shal.bus.ecovacs")

# App identity constants from the open-source deebot-client project — these are
# the public Android app's identifiers, required by the portal's request signing.
_CLIENT_KEY = "1520391301804"
_CLIENT_SECRET = "6c319b2a5cd3e66e39159c2e28f2fce9"
_AUTH_CLIENT_KEY = "1520391491841"
_AUTH_CLIENT_SECRET = "77ef58ce3afbe337da74aa8c5ab963a9"
_REALM = "ecouser.net"
_META = {
    "lang": "EN",
    "appCode": "global_e",
    "appVersion": "1.6.3",
    "channel": "google_play",
    "deviceType": "1",
}
_UA = "Dalvik/2.1.0 (Linux; U; Android 5.1.1; A5010 Build/LMY48Z)"
_TIMEOUT_S = 30.0

_EU = frozenset("at be bg ch cy cz de dk ee es fi fr gb gr hr hu ie is it li lt lu lv "
                "mc mt nl no pl pt ro se si sk sm uk".split())
_NA = frozenset("ca mx us".split())
_AS = frozenset("hk id il in jp kr my ph sa sg th tw vn".split())


def _continent(cc: str) -> str:
    override = os.environ.get("ECOVACS_CONTINENT")
    if override:
        return override.lower()
    if cc in _EU:
        return "eu"
    if cc in _NA:
        return "na"
    if cc in _AS:
        return "as"
    return "ww"


def _secret(name: str) -> str | None:
    """Process env first; on Windows fall back to the user-level registry value,
    so a terminal opened before the variable was stored still works.
    (Playground stand-in for DESIGN V2's pluggable secrets backend.)"""
    value = os.environ.get(name)
    if value:
        return value
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                return str(winreg.QueryValueEx(key, name)[0])
        except OSError:
            return None
    return None


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _sign(key: str, secret: str, params: Mapping[str, Any]) -> str:
    text = key + "".join(k + "=" + str(params[k]) for k in sorted(params)) + secret
    return _md5(text)


@register
class EcovacsCloudBus(Driver, Transport, MessageTransport):
    compatible = "ecovacs,cloud"
    kind = None  # sits at root (or behind any hop that can carry HTTPS — Phase 2)

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        cc = str(node.address).lower()
        if len(cc) != 2 or not cc.isalpha():
            raise LoadError(f"{node.path}: ecovacs,cloud address must be a two-letter "
                            f"country code (e.g. 'us'), got {node.address!r}")
        self.country = cc
        self.continent = _continent(cc)
        self.log = bus_logger("ecovacs", node.path)
        # ECOVACS_BUMPER_URL points this bus at a self-hosted Bumper server
        # (an all-in-one Ecovacs cloud emulator) instead of the real cloud.
        bumper = os.environ.get("ECOVACS_BUMPER_URL")
        self._base_override = bumper.rstrip("/") if bumper else None
        self.portal = (f"{self._base_override}/api" if self._base_override
                       else f"https://portal-{self.continent}.ecouser.net/api")
        # Credentials come from the node's `config:` mapping — ${ENV_VAR} refs
        # resolve at load (the documented home for secrets), literals pass through
        # for local-only files. Absence fails loudly at load, with the fix inline.
        cfg = getattr(node, "spec", {}).get("config") or {}
        self._email = cfg.get("user") or _secret("ECOVACS_EMAIL")
        self._password = cfg.get("password") or _secret("ECOVACS_PASSWORD")
        missing = [n for n, v in [("user", self._email),
                                  ("password", self._password)] if not v]
        if missing:
            raise LoadError(
                f"{node.path}: ecovacs,cloud is missing credentials: {', '.join(missing)}.\n"
                f"Declare them on the node —\n"
                f"  config:\n"
                f"    user: ${{ECOVACS_EMAIL}}      # env reference (recommended)\n"
                f"    password: ${{ECOVACS_PASSWORD}}   # or a literal, if this file stays local\n"
                f"and for env references, store the values once in PowerShell:\n"
                f'  [Environment]::SetEnvironmentVariable("ECOVACS_EMAIL", "you@example.com", "User")\n'
                f'  [Environment]::SetEnvironmentVariable("ECOVACS_PASSWORD", "...", "User")')
        # per-session app identity: the portal wants a stable device id + 8-char resource
        self._app_device_id = uuid.uuid4().hex
        self._resource = self._app_device_id[:8]
        self._uid: str | None = None
        self._token: str | None = None
        self._devices: list[dict] = []

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, str) or not addr.strip():
            raise LoadError(f"ecovacs,cloud: robot address must be a non-empty string "
                            f"(robot name / nick / did / serial), got {addr!r}")

    # -- lifecycle -----------------------------------------------------------

    def activate(self) -> None:
        """Login chain + device list. Lazy — runs on first use, under the bus lock.
        Credential presence was already validated at load (__init__)."""
        login = self._call_main_api("user/login",
                                    account=self._email, password=_md5(self._password))
        auth_code = self._get_auth_code(login["uid"], login["accessToken"])
        self._login_by_it_token(login["uid"], auth_code)
        self._devices = self._get_device_list()
        super().activate()
        logger.info("portal session open: %d device(s)", len(self._devices),
                    extra={"path": self.host.path, "txn": current_txn.get()})

    def close(self) -> None:
        self._uid = None
        self._token = None
        self._devices = []
        super().close()

    # -- the transport kind --------------------------------------------------

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:  # check -> activate -> talk, under the bus lock
            self.ensure_ready()
            dev = self._device_for(str(addr))
            cmd = msg["cmd"]
            data = msg.get("data")
            payload: dict[str, Any] = {
                "header": {"pri": "1", "ts": time.time(), "tzm": 480, "ver": "0.0.50"},
            }
            if data:
                payload["body"] = {"data": data}
            body = {
                "cmdName": cmd,
                "payload": payload,
                "payloadType": "j",
                "td": "q",
                "toId": dev["did"],
                "toRes": dev.get("resource", ""),
                "toType": dev.get("class", ""),
                "auth": self._auth_dict(),
            }
            query = urllib.parse.urlencode({
                "mid": dev.get("class", ""), "did": dev["did"], "td": "q",
                "u": self._uid, "cv": "1.67.3", "t": "a", "av": "1.3.1",
            })
            resp = self._portal_post(f"iot/devmanager.do?{query}", body)
            if resp.get("ret") != "ok":
                # the portal answered, but the robot may or may not have acted
                raise HopError(f"{cmd}: portal returned {resp.get('ret')!r} "
                               f"(errno={resp.get('errno')!r}, robot offline?)",
                               path=self.host.path, hop="ecovacs-cloud",
                               txn=current_txn.get(), delivered="unknown")
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug("cmd %s ok", cmd,
                             extra={"path": self.host.path, "addr": str(addr),
                                    "txn": current_txn.get()})
            return resp

    # -- auth chain ----------------------------------------------------------

    def _call_main_api(self, function: str, **args: str) -> dict:
        params: dict[str, Any] = dict(args)
        params["requestId"] = _md5(str(time.time()))
        params["authTimespan"] = int(time.time() * 1000)
        params["authTimeZone"] = "GMT-8"
        meta = {"country": self.country, "deviceId": self._app_device_id, **_META}
        params["authSign"] = _sign(_CLIENT_KEY, _CLIENT_SECRET, {**meta, **params})
        params["authAppkey"] = _CLIENT_KEY
        base = self._base_override or f"https://gl-{self.country}-api.ecovacs.com"
        url = (f"{base}/v1/private/"
               f"{self.country}/{meta['lang']}/{meta['deviceId']}/{meta['appCode']}/"
               f"{meta['appVersion']}/{meta['channel']}/{meta['deviceType']}/{function}")
        resp = self._get_json(url, params)
        if resp.get("code") != "0000":
            raise HopError(f"ecovacs login failed: code={resp.get('code')!r} "
                           f"msg={resp.get('msg')!r}",
                           path=self.host.path, hop="ecovacs-cloud",
                           txn=current_txn.get(), delivered="no")
        return resp["data"]

    def _get_auth_code(self, uid: str, access_token: str) -> str:
        params: dict[str, Any] = {
            "uid": uid,
            "accessToken": access_token,
            "bizType": "ECOVACS_IOT",
            "deviceId": self._app_device_id,
            "authTimespan": int(time.time() * 1000),
        }
        params["authSign"] = _sign(_AUTH_CLIENT_KEY, _AUTH_CLIENT_SECRET,
                                   {**params, "openId": "global"})
        params["authAppkey"] = _AUTH_CLIENT_KEY
        params["openId"] = "global"
        base = self._base_override or f"https://gl-{self.country}-openapi.ecovacs.com"
        url = f"{base}/v1/global/auth/getAuthCode"
        resp = self._get_json(url, params)
        if resp.get("code") != "0000":
            raise HopError(f"getAuthCode failed: code={resp.get('code')!r} "
                           f"msg={resp.get('msg')!r}",
                           path=self.host.path, hop="ecovacs-cloud",
                           txn=current_txn.get(), delivered="no")
        return resp["data"]["authCode"]

    def _login_by_it_token(self, uid: str, auth_code: str) -> None:
        resp = self._portal_post("users/user.do", {
            "edition": "ECOGLOBLE",
            "userId": uid,
            "token": auth_code,
            "realm": _REALM,
            "resource": self._resource,
            "org": "ECOWW",
            "last": "",
            "country": self.country.upper(),
            "todo": "loginByItToken",
        })
        if resp.get("result") != "ok":
            raise HopError(f"portal loginByItToken failed: {resp.get('error')!r}",
                           path=self.host.path, hop="ecovacs-cloud",
                           txn=current_txn.get(), delivered="no")
        self._uid = resp["userId"]
        self._token = resp["token"]

    def _get_device_list(self) -> list[dict]:
        resp = self._portal_post("users/user.do", {
            "userid": self._uid, "auth": self._auth_dict(), "todo": "GetDeviceList",
        })
        if resp.get("result") == "ok" and resp.get("devices"):
            return resp["devices"]
        # newer accounts: appsvr endpoint
        resp = self._portal_post("appsvr/app.do", {
            "userid": self._uid, "auth": self._auth_dict(), "todo": "GetGlobalDeviceList",
        })
        if resp.get("ret") == "ok" or resp.get("result") == "ok":
            return resp.get("devices") or []
        raise HopError("device list failed on users/user.do and appsvr/app.do",
                       path=self.host.path, hop="ecovacs-cloud",
                       txn=current_txn.get(), delivered="no")

    def _auth_dict(self) -> dict:
        return {"with": "users", "userid": self._uid, "realm": _REALM,
                "token": self._token, "resource": self._resource}

    def _device_for(self, addr: str) -> dict:
        want = addr.strip().lower()
        for dev in self._devices:
            keys = (dev.get("did"), dev.get("name"), dev.get("nick"),
                    dev.get("deviceName"), dev.get("sn"))
            if any(isinstance(v, str) and v.lower() == want for v in keys):
                return dev
        known = [dev.get("nick") or dev.get("deviceName") or dev.get("name") or "?"
                 for dev in self._devices]
        raise HopError(f"no robot matching {addr!r} on this account "
                       f"(found: {', '.join(known) or 'none'})",
                       path=self.host.path, hop="ecovacs-cloud",
                       txn=current_txn.get(), delivered="no")

    # -- http ------------------------------------------------------------------

    def _get_json(self, url: str, params: Mapping[str, Any]) -> dict:
        req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params),
                                     headers={"User-Agent": _UA})
        return self._do(req)

    def _portal_post(self, path: str, body: Mapping[str, Any]) -> dict:
        req = urllib.request.Request(
            f"{self.portal}/{path}", data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "User-Agent": _UA})
        return self._do(req)

    def _do(self, req: urllib.request.Request) -> dict:
        # error text carries the URL path only — query strings hold credentials
        where = req.full_url.split("?")[0]
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            raise HopError(f"HTTP {e.code} from {where}", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered="unknown") from e
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            delivered = "no" if isinstance(reason, ConnectionRefusedError) else "unknown"
            raise HopError(f"request failed: {reason} ({where})", path=self.host.path,
                           hop="ecovacs-cloud", txn=current_txn.get(),
                           delivered=delivered) from e
        except TimeoutError as e:
            raise HopTimeout(f"ecovacs request ({where})", path=self.host.path,
                             hop="ecovacs-cloud", txn=current_txn.get(),
                             delivered="unknown") from e
