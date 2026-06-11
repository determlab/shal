---
name: shal-build-driver
description: Implement a new SHAL device driver (sensor, actuator, robot...) bound by a compatible string. Use when adding support for a specific device on top of an existing bus/transport kind, or when reviewing a driver.
---

# Build a SHAL driver

A driver is the code bound to a node by its `compatible` id. It talks through
the PARENT bus's transport kind and exposes typed **capabilities** — user code
depends on the capability Protocol, never on your class.

Pick the device's `compatible` id and target domain library (`drivers/sensors`,
`drivers/instruments`, `drivers/data`, …) from
[docs/CATALOG.md](../../../docs/CATALOG.md) — claim it there before you start.

## Skeleton

```python
from typing import Protocol, runtime_checkable
from shal import Driver, idempotent, register
from shal.transport import ByteTransport, Read, Write

@runtime_checkable
class TemperatureSensor(Protocol):          # capability: semantic contract
    def read_celsius(self) -> float: ...    # UNIT IN THE NAME, always

@register                                    # or shal.drivers entry point
class MyTemp(Driver, TemperatureSensor):
    compatible = "vendor,my-temp"            # lowercase vendor,part
    kind = ByteTransport                     # what the parent bus MUST provide

    @idempotent                              # reads: safe to auto-retry
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return ((raw[0] << 4) | (raw[1] >> 4)) * 0.0625
```

## Framework-injected attributes (after bind)

`self.node` (the tree node) · `self.bus` (parent transport) · `self.addr`
(this node's address) · `self.log` (LoggerAdapter pre-bound with path/id/txn —
structured fields as kwargs: `self.log.debug("conv ready", event="...")`).

## The rules that matter

1. **`kind` declares your dependency.** The loader fails at setup if the parent
   bus doesn't provide it (`kinds()` check) — you never test for it yourself,
   and you NEVER use `hasattr` on the bus.
2. **Idempotency marking is a safety decision, not a convenience.**
   - `@idempotent` ONLY on ops that can run twice with no extra side effect
     (reads, absolute setpoints re-asserted). They get reconnect-once/retry-once
     for free.
   - Everything else (relative moves, counters, fire-and-forget commands) stays
     unmarked: a `HopError(delivered="unknown")` reaches the USER, who decides.
     Never catch-and-retry transport errors inside a driver.
3. **Payloads are yours; transport is not.** You know your device's register
   map / JSON commands; you never open sockets, spawn processes, or build
   shell strings. If you need a new way to reach hardware, that's a bus
   (see shal-build-bus).
4. **Public method = capability op.** The framework wraps every public method:
   txn id assignment, DEBUG call/raise traces, audit records for non-idempotent
   ops. Keep helpers underscore-prefixed so they aren't wrapped or audited.
5. **Capabilities are shared contracts.** Prefer an existing Protocol
   (`shal.capabilities`) over inventing one; specify units, ranges, error
   behavior in the docstring. Actuator capabilities must implement
   `safe_state()` (watchdog hook — enforced in Phase 2).
6. **Device-said-no is not a HopError.** If transport succeeded but the device
   answered with an error code, raise a driver-level error (subclass
   `shal.Error`) — delivery was certain, so the retry machinery must not see it.

## Make it LLM-callable (optional)

To expose ops as tools an LLM agent can discover and call, add `@shal.op`
metadata and opt into enforcement:

```python
class MyTemp(Driver, TemperatureSensor):
    compatible = "vendor,my-temp"
    kind = ByteTransport
    llm_ready = True              # bind FAILS if any op lacks @shal.op

    @idempotent
    @op("Read the current temperature. Call when you need it now.",
        unit="celsius", side_effect="none")
    def read_celsius(self) -> float: ...
```

`side_effect` is `"none"` (read), `"write"`, or `"actuator"` — if omitted it's
inferred from `@idempotent`. Then `hal.tool_schemas()` emits Anthropic tool-use
definitions, `hal.tool_catalog()` reports side-effects for gating, and
`hal.call_tool(name, args)` dispatches (a delivery-unknown write is reported,
never auto-retried). Input schemas come from your type hints — annotate params.

## Registration

```toml
[project.entry-points."shal.drivers"]
"vendor,my-temp" = "my_pkg.my_temp:MyTemp"
```

Two installed packages claiming the same `compatible` fail the load loudly (no
silent override); a topology disambiguates with a node `from: <distribution>`,
or call `shal.register(cls, override=True)` to deliberately shadow.

In-process `@shal.register` is fine for tests and local work.

## Tests to write (minimum)

- A sim model for `shal,sim-i2c` (`@sim_model("vendor,my-temp")`) or a fake
  MessageTransport bus, so the driver runs with zero hardware.
- One test per capability op against the sim, checking VALUES (decode math).
- Retry behavior: idempotent op survives one `fail_next`; non-idempotent op
  propagates `delivered="unknown"` untouched.
- `isinstance(driver, <CapabilityProtocol>)` — the runtime_checkable contract.
- Load-time: wrong parent kind fails with a clear LoadError.
