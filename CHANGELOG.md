# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
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
