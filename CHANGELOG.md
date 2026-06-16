# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **In-package agent guide + `shal docs`** (#55) — a provider-neutral "add a device"
  guide now ships **inside the wheel** (`shal/AGENT_GUIDE.md`); `shal docs` prints it. A
  pip-only agent can author a working wrap-a-library driver — root driver, `@op`
  side-effects, the read-freshness rule, load with `--drivers`, read with `shal probe` —
  with no GitHub and no source-diving. The demo slice of the full Authoring Kit
  (#22/#23/#24). Per `docs/ARCHITECTURE.md` D7 / Principle 3 (self-sufficient from the package).
- **`shal` CLI — the base front door** (#54) — `shal probe <topology>` prints a real
  device reading and exits, `shal tools` lists the device tools (read / gated), and
  `shal mcp` serves to an MCP host (the adapter); `--drivers` loads local drivers. The
  legacy `shal-mcp` command is now an alias of `shal mcp`. SHAL stands on its own
  without MCP — the read path no longer hides under a host-named command. Per
  `docs/ARCHITECTURE.md` D11.
- **MCP server — the agent-host front door** (#25/#26/#27) — `shal-mcp <topology.yaml>`
  serves a SHAL topology to any MCP host (Claude Code/Desktop, …) as typed, gated
  tools. Reads run free; a state-changing op is **never executed on first call** —
  it returns an `approval_required` ticket that a human authorizes via the separate,
  destructive-flagged `shal_approve` tool (host-agnostic in-band approval). `--approve
  auto` (or `SHAL_APPROVE=auto`) opts into free writes and records the choice in the
  audit log. The `mcp` SDK is an optional extra (`pip install pyshal[mcp]`); the core
  stays at two dependencies. The SHAL→MCP mapping lives in a dependency-free
  `shal.mcp.Bridge` (fully unit-tested without the SDK).
- **`shal-mcp --drivers`** (#47) — load local/unpackaged driver modules before
  serving: `shal-mcp my.yaml --drivers ./drivers/` imports a `.py` file or a whole
  directory (repeatable) so each driver's `@shal.register` runs. Makes
  bring-your-own-driver setups runnable from the CLI without packaging every driver,
  while the topology YAML stays pure data (imports are operator-controlled on the
  command line). An unresolvable `compatible` now points at the flag.
- **`shal-mcp --probe`** (#39) — a one-shot, human-runnable read: `shal-mcp my.yaml
  --probe` prints every device's current readings and exits; `--probe <tool>` runs
  one named read. **No MCP host required**, and (like the Bridge) it needs no `mcp`
  extra. Reads only — writes are listed but never run. The "install → see a real
  value in one command" path, instead of dead-ending at a stdio server.
- **`MediaPlayer` capability + a Sonos example driver** (#28) — a new `MediaPlayer`
  capability (play / pause / stop / next / previous / volume as benign writes;
  now-playing / state / volume as free reads). The `sonos,speaker` driver that
  implements it — the canonical "wrap an existing Python library" root driver
  (`kind=None`, wraps `soco`, sim-first) — ships as a **repo example**
  (`examples/demos/sonos/`), **not** bundled in the installed package, keeping the
  core device-agnostic (the front door points at a topology YAML; devices are
  examples/community packages).
- **`shal.load()` accepts an in-memory topology dict** (#29) — not just a file
  path — the shape a programmatic setup flow builds.
- **Approval-ticket hardening — a "no" is first-class and final** (#36) — the MCP
  bridge gains a `shal_deny` tool that discards a pending action; because the
  ticket is consumed on either decision, a denied (or already-run) `approval_id`
  can never be replayed as an approval. Every ticket transition — `requested`,
  `approved`, `denied` — is now written to `shal.audit` correlated by the
  `approval_id`, so a refusal is exactly as visible as a successful action. The
  approval stays bound to the `(tool, arguments)` the human saw — args smuggled
  into the confirm call are ignored — and pending tickets are in-memory only, so a
  restart fails closed. Regression-tested.

### Fixed
- **Reads must be live, not a stale default** (#53) — documented the read-freshness
  contract in the SDK (`docs/SDK.md` §1b): a read returns a value **only if the device
  answered this call**, otherwise it raises `shal.HopError` — never a cached / seeded /
  default value dressed up as live. Guards the "trust what the agent reads" promise,
  especially when wrapping a third-party library that returns a default before the
  device responds (the framework can't police that — the driver author must). Per
  `docs/ARCHITECTURE.md` D12.
- **Docs reachable for `pip` users** (#40) — README links were repo-relative, so
  they 404'd on PyPI and for anyone who only `pip install`ed. They're now absolute
  GitHub URLs. The **driver-authoring guide** (`docs/SDK.md`) — previously unlinked —
  is now surfaced in the README's Documentation section, and `shal-mcp --help` points
  at the docs + the SDK guide.
- **Approval gate fail-open** (#19) — an un-annotated, non-idempotent op on a
  device driver is now inferred **fail-closed** as `"actuator"` (gated) instead of
  `"write"` (ungated), so a forgotten `side_effect` stops for approval rather than
  silently reaching hardware. Reads (`@idempotent`) stay ungated, and an explicit
  `side_effect="write"` remains a benign, ungated state change. Makes the README's
  "asks before it moves … unbypassable" claim true by default. Regression-tested.
- **Secret leak in logs/errors** (#20) — credentials carried in an address
  (`https://user:pass@host`, or userinfo on a `host:port`) and URL query strings
  no longer reach `HopError` text or bus logs. A single `redact_url()` sanitizer
  strips userinfo + query/fragment, keeping the bare `scheme://host[:port]/path`
  endpoint (operational context, not a secret). Applied uniformly to the `http`,
  `tcp`, and `scpi-raw` buses. Regression-tested.

## [0.1.0] - 2026-06-15

First PyPI release ([`pyshal`](https://pypi.org/project/pyshal/) — import name `shal`).
The Phase 1 synchronous core plus the driver/instrument, conformance/SDK,
declared-limits, and human-in-the-loop approval work that landed before publishing.

### Fixed
- **Authoring-contract drift** (#15) — aligned the `shal-build-*` skills with
  `docs/SDK.md` and the framework so a driver copied verbatim from the
  `shal-build-driver` skeleton passes `conformance.check_driver` (now regression-
  tested): the skeleton uses the blessed `shal.TemperatureSensor` and includes the
  required `llm_ready` + `@op` (no longer framed as "optional"). Also fixed the
  `src/shal/schema/` path in `shal-build-yaml`, completed its bundled-id list
  (added `shal,scpi-raw`/`shal,sim-scpi`/`shal,sim-msg`, pointing at
  `shal.catalog()` as authoritative), added `txn=` to the documented `HopError`
  signature, and documented the actuator `safe_state()` hook in the SDK.

### Added
- **Human-in-the-loop actuation gate** (#14) — actuator and destructive/config
  ops (`@shal.op(side_effect="actuator"|"config")`) now stop for an injectable
  `Approver` *after* the limit check and *before* any bus I/O. The gate lives in the capability-wrapper, so neither the
  tool surface (`call_tool`) nor the raw path (`get_device().method()`) can bypass
  it. SHAL ships the mechanism + a safe default (`ConsoleApprover`: prompt when
  interactive, deny when headless) plus `AutoApprove`/`DenyAll`/`CallableApprover`;
  install one with `shal.set_approver(...)` or the `shal.approver(...)` context
  manager. Refusal raises `shal.ApprovalDenied` (nothing sent) and `call_tool`
  returns `{"ok": False, "rejected": "approval"}`. Every decision (approved/denied)
  is written to `shal.audit`. Order is always limits → approval → I/O.
- **Declared operating limits** (#10) — `@shal.op(params=...)` takes JSON-Schema
  fragments per parameter; the merged schema is advertised verbatim in
  `tool_schemas()`/`catalog()` AND enforced by the framework before any bus I/O
  (`shal.LimitError`; rejected writes are audited `outcome=rejected`). Two
  narrow-only layers stack on top: `driver.op_limits()` for address-dependent
  ratings and YAML `config.limits` for installation policy — widening fails the
  load naming both numbers.
- **Conformance kit** (#10) — `shal.conformance.check_driver()` self-certifies a
  driver: static checks (llm_ready, @op metadata, schema well-formedness) plus
  live probes on a sim topology (limits actually reject pre-I/O, writes actually
  hit the audit channel, capabilities actually isinstance).
- **Generic sim buses** (#10) — `shal,sim-scpi` (`@scpi_sim_model`) and
  `shal,sim-msg` (`@msg_sim_model`) mirror sim-i2c's model registry for the
  MessageTransport families; ships a DP832 model for hermetic SCPI coverage.
- **Driver SDK guide** (#10) — `docs/SDK.md`: the complete authoring contract
  (driver anatomy, capabilities, transport dialects, limits, sims, conformance);
  with the skills, writing a driver requires reading zero SHAL internals. New
  `shal-generate-driver` skill: the documentation→driver generation recipe.
- **Core I²C drivers** (#2/#3) — `microchip,mcp9808` (`TemperatureSensor`),
  `ti,ads1115` (new `ADC` capability), `microchip,mcp23017` (new `GPIOExpander`
  capability). All dependency-free, sim-backed (`shal,sim-i2c` models), hermetic tests.
- **SCPI instrument stack** (#2, Wave 1) — `shal,scpi-raw` bus (SCPI over a raw TCP
  socket, the lab :5025 convention; stdlib sockets only, no VISA; plaintext with a
  required `insecure: true`), plus the first instrument drivers `rigol,dp832`
  (`PowerSupply`) and `keysight,34461a` (`DigitalMultimeter`) and their capability
  Protocols. End-to-end tested against a fake SCPI socket server (no hardware).
- **Driver `ti,ina219`** (#2, Wave 1) — I²C bus-voltage / current / power monitor,
  the first `PowerMonitor` capability. Dependency-free, sim-backed (`shal,sim-i2c`
  gains an `ina219` model), fully hermetic tests.
- **Node-level agent metadata** (#1) — optional `description:` (instance context
  blended into each tool's description, so an agent distinguishes like devices) and
  `expose: false` (omit a node from `tool_schemas()`/`tool_catalog()`/`call_tool()`
  while keeping it usable from Python) on any topology node. Additive; existing
  topologies are unaffected.
- **`shal.catalog()` authoring surface** (#1) — an introspection view of every
  registered driver/bus so an LLM (or a human) can construct a valid topology:
  `catalog()` returns compact summaries, `catalog(compatible)` the full detail.
  Most fields are derived (compatible, required parent kind, capability Protocol,
  ops); a class declares only the irreducible bits via an optional `authoring_meta()`
  classmethod (`address_schema` / `config_schema` / `child_address_schema` as
  JSON-Schema fragments). Op annotations map side-effects to MCP-style hint names
  (`readOnlyHint`/`idempotentHint`/`destructiveHint`), also added to `tool_catalog()`.
- **Topology includes** — a node may `use:` an external `template:` file to graft
  a reusable subtree (a board, a rack) without copy-paste, with `with:` parameter
  substitution (`${param}`), use-site key overrides, include chains, a cycle
  guard, and path confinement to the project tree. Still `yaml.safe_load` only —
  the splice happens in the loader, never via a YAML tag.
- **Registry collision policy** — two different classes claiming one `compatible`
  no longer silently overwrite (last-write-wins). The clash fails the load,
  naming each providing distribution; disambiguate with a node `from:` key,
  `register(..., override=True)`, or by uninstalling one. Re-registering the same
  class stays an idempotent no-op.
- **LLM tool surface** — `@shal.op(description, unit, side_effect)` metadata on
  capability ops; `Driver.llm_ready = True` enforces it at bind time.
  `hal.tool_schemas()` emits Anthropic tool-use definitions for every device op,
  `hal.tool_catalog()` reports per-op `side_effect`/idempotency for gating, and
  `hal.call_tool(name, args)` dispatches — a delivery-unknown write is reported,
  never silently retried. Buses are excluded (they provide transport, not
  capabilities).

- Topology loader: versioned YAML (`shal_version: 1`) with JSON Schema
  validation, global id uniqueness, address-grammar validation at load,
  `$ref` back-links, `${ENV_VAR}` resolution for addresses and `config:` values.
- Typed transport kinds: `ByteTransport`, `CommandTransport` (argv only),
  `MessageTransport`, `Stream` (Phase 2 placeholder); `kinds()` introspection.
- Driver model: registry keyed by `compatible`, entry-point group
  `shal.drivers`, `@shal.idempotent`, framework-owned retry
  (reconnect once / retry once for idempotent ops; delivery-unknown writes are
  never re-fired).
- Bundled buses: `shal,sim-i2c`, `shal,local`, `shal,ssh-host`,
  `shal,i2c-cli`, `shal,spi-cli`, `shal,tcp` (TLS default), `shal,http`,
  `nxp,pca9548` mux with per-mux selection cache.
- Bundled driver: `ti,tmp102` (`TemperatureSensor` capability).
- Lookup API: `shal.load()` context manager, `get_device()` by id/path with
  positional shorthand, deterministic leaf→root teardown.
- Error taxonomy: `LoadError`, `HopError` (`delivered: no|unknown`),
  `HopTimeout`, `Busy`, `Gap`.
- Observability: structured records with stable `event` keys and
  `path/hop/addr/txn/duration_ms` fields on every hop; WARNING on handled
  retries; DEBUG breadcrumbs before raising; `shal.audit` channel for
  actuator-style write ops (silent by default); `shal.logging` with
  `ConsoleFormatter`, `JSONFormatter`, and the `capture()` JSON-lines flight
  recorder.
- Packaging: PEP 621 metadata, `py.typed`, MIT license, CI workflow.
