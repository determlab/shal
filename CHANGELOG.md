# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **MCP server — the agent-host front door** (#25/#26/#27) — `shal-mcp <topology.yaml>`
  serves a SHAL topology to any MCP host (Claude Code/Desktop, …) as typed, gated
  tools. Reads run free; a state-changing op is **never executed on first call** —
  it returns an `approval_required` ticket that a human authorizes via the separate,
  destructive-flagged `shal_approve` tool (host-agnostic in-band approval). `--approve
  auto` (or `SHAL_APPROVE=auto`) opts into free writes and records the choice in the
  audit log. The `mcp` SDK is an optional extra (`pip install pyshal[mcp]`); the core
  stays at two dependencies. The SHAL→MCP mapping lives in a dependency-free
  `shal.mcp.Bridge` (fully unit-tested without the SDK).

### Fixed
- **Approval gate fail-open** (#19) — an un-annotated, non-idempotent op on a
  device driver is now inferred **fail-closed** as `"actuator"` (gated) instead of
  `"write"` (ungated), so a forgotten `side_effect` stops for approval rather than
  silently reaching hardware. Reads (`@idempotent`) stay ungated, and an explicit
  `side_effect="write"` remains a benign, ungated state change. Makes the README's
  "asks before it moves … unbypassable" claim true by default. Regression-tested.

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
