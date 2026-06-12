# ECOVACS Cloud — Account & Command Transport Protocol (DN20-CLOUD rev 1.1)

How an application reaches a DEEBOT N20 through the ECOVACS cloud: the
three-step authentication chain, device enumeration, and the command relay
endpoint. The device-level command payloads carried over this transport are
specified in *DN20-PROTO* (`deebot-protocol.md`); this document covers
everything between the application and that payload.

All exchanges are HTTPS, JSON over HTTP/1.1. Production hosts are regional:

| Service | Base URL |
|---|---|
| Main API (account login) | `https://gl-{cc}-api.ecovacs.com` |
| Open API (auth-code issue) | `https://gl-{cc}-openapi.ecovacs.com` |
| User portal (device list, commands) | `https://portal-{continent}.ecouser.net/api` |

`{cc}` is the account's lowercase two-letter country code (`us`, `de`, …);
`{continent}` derives from it per §2.

## 1. Application identity constants

Requests are signed with the published mobile-app identity. These values are
fixed and must be sent exactly:

| Constant | Value |
|---|---|
| `CLIENT_KEY` (main API) | `1520391301804` |
| `CLIENT_SECRET` (main API) | `6c319b2a5cd3e66e39159c2e28f2fce9` |
| `AUTH_CLIENT_KEY` (open API) | `1520391491841` |
| `AUTH_CLIENT_SECRET` (open API) | `77ef58ce3afbe337da74aa8c5ab963a9` |
| `REALM` | `ecouser.net` |
| `lang` | `EN` |
| `appCode` | `global_e` |
| `appVersion` | `1.6.3` |
| `channel` | `google_play` |
| `deviceType` | `1` |

Recommended `User-Agent`:
`Dalvik/2.1.0 (Linux; U; Android 5.1.1; A5010 Build/LMY48Z)`.

**Per-session identity:** generate one random 32-character lowercase hex
string per session as `deviceId` (the *application* device id, not the
robot's), and use its **first 8 characters** as `resource`. Both stay constant
for the life of the session.

## 2. Continent derivation

| Country code in | `{continent}` |
|---|---|
| at be bg ch cy cz de dk ee es fi fr gb gr hr hu ie is it li lt lu lv mc mt nl no pl pt ro se si sk sm uk | `eu` |
| ca mx us | `na` |
| hk id il in jp kr my ph sa sg th tw vn | `as` |
| anything else | `ww` |

The environment variable `ECOVACS_CONTINENT` (values `eu`/`na`/`as`/`ww`),
when set, overrides the derived continent.

## 3. Request signing

Main-API and Open-API requests carry an `authSign` parameter:

```
authSign = MD5( key  +  concat over params sorted by name of (name + "=" + str(value))  +  secret )
```

- lowercase hexadecimal digest of the UTF-8 string;
- *params* = every parameter in the signed set (each step below says exactly
  which), sorted by parameter **name**, concatenated as `name=value` with **no
  separators** between pairs;
- `key`/`secret` = `CLIENT_KEY`/`CLIENT_SECRET` for the main API,
  `AUTH_CLIENT_KEY`/`AUTH_CLIENT_SECRET` for the open API.

The account password is never sent in clear: send
`MD5(plaintext password)` (lowercase hex) as the `password` parameter.

## 4. Step 1 — account login (main API)

```
GET {main}/v1/private/{country}/{lang}/{deviceId}/{appCode}/{appVersion}/{channel}/{deviceType}/user/login
```

(`{main}` = the main-API base; path segments are the §1 constants plus the
country code and session `deviceId`.)

Query parameters:

| Param | Value |
|---|---|
| `account` | account e-mail |
| `password` | `MD5(plaintext)` |
| `requestId` | any fresh 32-hex nonce (e.g. `MD5(str(time()))`) |
| `authTimespan` | current epoch time in **milliseconds** |
| `authTimeZone` | `GMT-8` |
| `authSign` | §3 signature, main-API key pair |
| `authAppkey` | `CLIENT_KEY` |

**Signed set** = the meta params `{country, deviceId, lang, appCode,
appVersion, channel, deviceType}` **plus** `{account, password, requestId,
authTimespan, authTimeZone}`. (`authSign`/`authAppkey` themselves are not
signed.)

Response:

```json
{"code": "0000", "msg": "...", "data": {"uid": "<userId>", "accessToken": "<token>"}}
```

`code` `"0000"` (a string) = success; anything else is a login failure (bad
credentials, rate limit) — the request was refused, nothing was delivered to
any robot.

## 5. Step 2 — auth code (open API)

```
GET {open}/v1/global/auth/getAuthCode
```

Query parameters:

| Param | Value |
|---|---|
| `uid` | `uid` from step 1 |
| `accessToken` | from step 1 |
| `bizType` | `ECOVACS_IOT` |
| `deviceId` | session `deviceId` |
| `authTimespan` | epoch milliseconds |
| `authSign` | §3 signature, **open-API** key pair |
| `authAppkey` | `AUTH_CLIENT_KEY` |
| `openId` | `global` |

**Signed set** = `{uid, accessToken, bizType, deviceId, authTimespan, openId}`
— note `openId=global` IS part of the signed set even though it is also sent
as a plain parameter.

Response: `{"code": "0000", "data": {"authCode": "<code>"}}`; same `"0000"`
success convention.

## 6. Step 3 — portal session (`loginByItToken`)

```
POST {portal}/users/user.do
Content-Type: application/json
```

```json
{
  "edition": "ECOGLOBLE",
  "userId": "<uid from step 1>",
  "token": "<authCode from step 2>",
  "realm": "ecouser.net",
  "resource": "<8-char session resource>",
  "org": "ECOWW",
  "last": "",
  "country": "<CC, UPPERCASE>",
  "todo": "loginByItToken"
}
```

Response: `{"result": "ok", "userId": "<portalUserId>", "token": "<portalToken>"}`.
Portal endpoints use `result`/`"ok"` (not `code`/`"0000"`). Keep
`portalUserId` + `portalToken`: every subsequent portal call authenticates
with the **auth dict**

```json
{"with": "users", "userid": "<portalUserId>", "realm": "ecouser.net",
 "token": "<portalToken>", "resource": "<resource>"}
```

## 7. Device enumeration — `GetDeviceList`

```
POST {portal}/users/user.do
{"userid": "<portalUserId>", "auth": <auth dict>, "todo": "GetDeviceList"}
```

Response: `{"result": "ok", "devices": [ ... ]}`. Each device record contains
at least:

| Field | Meaning |
|---|---|
| `did` | the robot's device id — used to address commands |
| `name` / `nick` / `deviceName` / `sn` | human identifiers (any may be used to pick a robot) |
| `class` | model class code |
| `resource` | the robot's resource string |

Newer accounts may return an empty list here; the fallback is
`POST {portal}/appsvr/app.do` with
`{"userid", "auth", "todo": "GetGlobalDeviceList"}`, which answers
`{"ret": "ok", "devices": [...]}`.

## 8. Command relay — `iot/devmanager.do`

Every DN20-PROTO command `{"cmd": C, "data": D}` is delivered as:

```
POST {portal}/iot/devmanager.do?mid={class}&did={did}&td=q&u={portalUserId}&cv=1.67.3&t=a&av=1.3.1
Content-Type: application/json
```

```json
{
  "cmdName": C,
  "payload": {
    "header": {"pri": "1", "ts": <epoch seconds>, "tzm": 480, "ver": "0.0.50"},
    "body": {"data": D}
  },
  "payloadType": "j",
  "td": "q",
  "toId": "<did>",
  "toRes": "<device record's resource, or \"\">",
  "toType": "<device record's class, or \"\">",
  "auth": <auth dict>
}
```

- When `D` is `null`/absent, **omit** `payload.body` entirely (send only the
  `header`).
- `payloadType` `"j"` (JSON) and `td` `"q"` (query) are literal.

Response: `{"ret": "ok", "resp": <the DN20-PROTO response envelope>}` — i.e.
exactly the `{"ret", "resp"}` object of the device protocol document.
`ret != "ok"` (typically with an `errno`) means the **portal** failed or the
robot is offline; the command may or may not have reached the robot, so its
delivery state is *unknown* — do not blind-retry actuations. Robot-level
refusals come back inside `resp.body.code` per DN20-PROTO §2.

## 9. Credentials & configuration conventions

- Account credentials are supplied per integration node as configuration keys
  `user` and `password`. The conventional environment variables are
  **`ECOVACS_EMAIL`** and **`ECOVACS_PASSWORD`** — configuration should
  reference them (e.g. `user: ${ECOVACS_EMAIL}`) rather than embed literals.
  Credential values must never appear in logs or error messages (error text
  may name the URL *path*, never the query string).
- **`portal_url` override** — for test benches and self-hosted portals
  (e.g. an emulator on `http://127.0.0.1:<port>`), the configuration key
  `portal_url` (environment fallback: **`ECOVACS_PORTAL_URL`**) replaces *all
  three* §0 base URLs with the single given origin:
  - Main API base → `{portal_url}` (so step 1 is
    `{portal_url}/v1/private/.../user/login`)
  - Open API base → `{portal_url}` (so step 2 is
    `{portal_url}/v1/global/auth/getAuthCode`)
  - Portal base → `{portal_url}/api` (so `users/user.do`,
    `appsvr/app.do`, `iot/devmanager.do` live under `{portal_url}/api/...`)

  Plain `http://` is permitted only via this explicit override; production
  traffic is HTTPS.

## 10. Worked examples (test vectors)

**W1 — password hash:** plaintext `hunter2` →
`password = 2ab96390c7dbe3439de74d0c9b0b1767`.

**W2 — step-1 signature.** Account `jane@example.com`, password `hunter2`,
country `us`, session `deviceId = 3f8e2a14c9d04b6aa1b2c3d4e5f60718`,
`requestId = 9a1de8c0a9b6f1f3e2d4c5b6a7980102`,
`authTimespan = 1718000000000`, `authTimeZone = GMT-8`. The signed set sorted
by name concatenates to:

```
account=jane@example.comappCode=global_eappVersion=1.6.3authTimeZone=GMT-8authTimespan=1718000000000channel=google_playcountry=usdeviceId=3f8e2a14c9d04b6aa1b2c3d4e5f60718deviceType=1lang=ENpassword=2ab96390c7dbe3439de74d0c9b0b1767requestId=9a1de8c0a9b6f1f3e2d4c5b6a7980102
```

so `authSign = MD5("1520391301804" + <that string> + "6c319b2a5cd3e66e39159c2e28f2fce9")`
= **`19d8c5e101d5b4c8b51a39e7c4b9fc0f`**.

**W3 — step-2 signature.** `uid = 20240612abcdef01`,
`accessToken = tk-9f8e7d6c`, `bizType = ECOVACS_IOT`, same `deviceId`,
`authTimespan = 1718000000123`, `openId = global` →
`authSign = MD5("1520391491841" + "accessToken=tk-9f8e7d6cauthTimespan=1718000000123bizType=ECOVACS_IOTdeviceId=3f8e2a14c9d04b6aa1b2c3d4e5f60718openId=globaluid=20240612abcdef01" + "77ef58ce3afbe337da74aa8c5ab963a9")`
= **`c5ef28d621d0cdc0be193b8b7e96dc1b`**.

**W4 — relay envelope.** Sending DN20-PROTO E4 (`charge`, `{"act": "go"}`) to
robot `did = did-bot1`, class `p1jij8`, resource `atag`:

```json
POST {portal}/iot/devmanager.do?mid=p1jij8&did=did-bot1&td=q&u=u-bench&cv=1.67.3&t=a&av=1.3.1

{"cmdName": "charge",
 "payload": {"header": {"pri": "1", "ts": 1718000123.4, "tzm": 480, "ver": "0.0.50"},
             "body": {"data": {"act": "go"}}},
 "payloadType": "j", "td": "q",
 "toId": "did-bot1", "toRes": "atag", "toType": "p1jij8",
 "auth": {"with": "users", "userid": "u-bench", "realm": "ecouser.net",
          "token": "tk-bench-1", "resource": "3f8e2a14"}}
```

and the portal answers `{"ret": "ok", "resp": { ... DN20-PROTO body ... }}`.
