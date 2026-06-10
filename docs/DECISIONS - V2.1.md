# SHAL — Implementation decisions (v2.1 addendum)

Resolves DESIGN V2 open questions needed for Phase 1 (sync core). 2026-06-10.

## Resolved

1. **JSON Schema** — authored at `schema/shal-v1.schema.json`. Structural rules only;
   semantic checks (id uniqueness, address grammar, driver installed, $ref targets)
   stay in the loader, after schema validation.

2. **`get_device` positional shorthand** — adopted. One positional arg: leading `/`
   means path, otherwise id. `hal.get_device("ambient_temp")` ==
   `hal.get_device(id="ambient_temp")`. Keyword forms remain.

3. **Error taxonomy**
   - `shal.Error` — base.
   - `shal.LoadError(Error)` — anything wrong before runtime: schema, unknown
     `compatible`, duplicate id, bad address grammar, unresolved `$ref`, missing env var.
   - `shal.HopError(Error)` — runtime hop failure. Fields: `path`, `hop`, `txn`,
     `delivered: "no" | "unknown"`. Wrapped with `raise … from e`.
   - `shal.HopTimeout(HopError)` — field `which: "hop" | "budget"`.
   - `shal.Busy(Error)` — mux channel pinned by a subscription (Phase 2).
   - `shal.Gap` — an *event*, not an exception.

4. **Driver base API** (the spec the examples implied)
   - `class Driver`: class attrs `compatible: str` (required), `kind: type | None`
     (required transport kind of the parent bus; `None` for pure bus nodes at root).
   - Framework-injected instance attrs: `node`, `bus` (parent bus), `addr`, `log`
     (LoggerAdapter bound to `shal.driver.<compatible>` with `path`/`id`/`txn`).
   - `@shal.idempotent` marks capability methods safe to auto-retry.
   - At bind time the framework wraps every public capability method: assigns a `txn`
     id (contextvar), and adds retry (reconnect once, retry once) **only** to
     `@idempotent` methods. Unmarked ops propagate `HopError(delivered=…)` untouched.
   - Entry-point group: **`shal.drivers`** (one group; bus-ness comes from transport
     kinds, per decision 3). Bundled drivers register explicitly in core.

5. **Watchdog mechanics** — one monotonic-deadline timer thread per HAL instance
   servicing all `watchdog_ms` nodes (not a thread per node); buses report
   connection-loss to it; trips call `driver.safe_state()`, log WARNING. *Designed
   here, implemented in Phase 2 with async/actuators.*

6. **Phase 1 scope** — sync only: loader, transport kinds, registry, driver base,
   lookup API, logging, `shal.sim` bus, `ti,tmp102` driver, tests. `routes:` parses
   but fails load with "failover not implemented in this version" (honest, loud).
   Mux, streaming, watchdog, ssh bus → Phase 2.

## Phase 1.1 — bundled bus families

`shal,local` + `shal,ssh-host` (CommandTransport; ssh via system client, argv +
`--`, ControlMaster reuse, exit 255 = transport failure, delivered=unknown);
`shal,i2c-cli` + `shal,spi-cli` (ByteTransport rendered to i2ctransfer/spi-pipe
argv over the parent CommandTransport); `shal,tcp` (MessageTransport, JSON-lines
framing `{"addr":…,"payload":…}`, TLS default) + `shal,http` (MessageTransport,
POST JSON to `<base>/<addr>`, https required unless `insecure: true`);
`nxp,pca9548` mux per the v2 mux design. Core gained one hook for muxes:
`Driver.provide_child_bus(child)` → `Node.exposed_bus` (a parent driver may
expose a distinct bus per child). Async families (uart/can/ble/mqtt) remain
Phase 2 — they require the held-channel Stream contract.

## Still open (unchanged)

`agent` wire protocol; hotplug/discovery; metrics surface; capability RFC paperwork;
asyncio internals for `stream()`.
