# Connect Claude to a SHAL topology (60 seconds)

SHAL serves your topology to Claude as **gated tools** over MCP. The **core**
(`shal mcp`) is agent-agnostic; this folder holds the **Claude-specific**
registration. (Other hosts get their own `integrations/<host>/` — the core never
knows which agent is calling.)

## 1. Secrets — put them in a `.env` (never in the host config)

Beside your topology, create a `.env`:

```bash
ECOVACS_EMAIL=you@example.com
ECOVACS_PASSWORD=super-secret
```

Your topology references them as `${ECOVACS_PASSWORD}`. `shal` loads the `.env`
**automatically** when it starts — so credentials are resolved by SHAL itself and
**never written into Claude's config file**.

- **Add `.env` to `.gitignore`** (SHAL warns if you forget). It never leaves your machine.
- A real environment variable **overrides** the `.env` (handy in CI).

## 2. Register the server (one command — don't hand-edit JSON)

Delegate to Claude's own CLI:

```bash
claude mcp add shal -- shal mcp /abs/path/to/lab.yaml
```

Using unpackaged drivers? Add them on the same line:

```bash
claude mcp add shal -- shal mcp /abs/path/to/lab.yaml --drivers /abs/path/to/drivers/
```

(One-time: the MCP transport needs the extra — `pip install "pyshal[mcp]"`.)

## 3. Use it

Ask Claude to **read** a device (runs immediately) or **change** one — a write
**pauses** and returns an `approval_required` ticket you confirm with the
`shal_approve` tool. Nothing reaches hardware until you approve.

---

*Why this lives here and not in the core:* per `docs/ARCHITECTURE.md` **D16**,
host-specific glue (Claude's `claude mcp add`) stays in `integrations/<host>/`.
The agent-agnostic core only **serves** the topology and **resolves** the `.env`.
