# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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

## [0.1.0] - 2026-06-10

Phase 1: the synchronous core.

### Added
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
