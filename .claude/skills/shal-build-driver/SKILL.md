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
   - A `write`/`actuator` op you want **audited** must NOT be `@idempotent` — the
     audit trail logs only non-idempotent commands, and the conformance kit fails
     a write that produces no audit record. Mark reads idempotent; leave
     writes/actuators unmarked (even absolute setpoints).
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

**`actuator` ops are gated (issue #14).** Mark physical-motion ops
`side_effect="actuator"` and the framework consults the active `Approver` after
the limit check and before any bus I/O — on both the tool surface and the raw
`get_device().method()` path (unbypassable). You write nothing extra in the body;
a refusal raises `shal.ApprovalDenied` (nothing sent) and every decision is
audited (`outcome` = `approved`/`denied`). The host installs the policy
(`shal.set_approver(...)` / `with shal.approver(...)`); SHAL ships `AutoApprove`
(for sim/CI/tests), `DenyAll`, `CallableApprover`, and the default
`ConsoleApprover`. Use `actuator` for motion/dispense; `write` is audited but not
gated. Order is always limits → approval → I/O.

## Operating limits (declare once — advertised AND enforced)

If the documentation states a safe range for a settable parameter, declare it
as a JSON-Schema fragment on the op — MANDATORY wherever a documented range
exists:

```python
@op("Set this channel's output voltage (absolute setpoint).",
    unit="volt", side_effect="write",
    params={"volts": {"minimum": 0.0, "maximum": 32.0}})   # from the datasheet
def set_voltage(self, volts: float) -> None: ...           # body stays check-free
```

One declaration gives you all of: the constraint advertised verbatim in the
tool schema (the model self-polices), FRAMEWORK enforcement before any bus I/O
(`shal.LimitError`; the device never sees the command; the attempt is audited
`outcome=rejected`), and a `catalog()` manifest entry. Allowed keywords:
`minimum`/`maximum`/`exclusiveMinimum`/`exclusiveMaximum`/`enum`/`const`/
`multipleOf`/`type`/`description`/`examples`. `enum` covers mode strings.

Address-dependent ratings (PSU channel 3 is the 5 V rail) — override the
narrow-only hook:

```python
def op_limits(self):
    return {"set_voltage": {"volts": {"maximum": 5.3}}} if self.ch == 3 else {}
```

Rig owners tighten further per node in YAML (`config: {limits: ...}` — see
shal-build-yaml); both layers may only TIGHTEN, widening fails the load.
Cross-parameter envelopes (V×I ≤ W) stay imperative in the body via a
`shal.Error` subclass — say so in the op description.

## Certify (the definition of done)

```python
from shal import conformance
report = conformance.check_driver("vendor,my-temp", topology="tests/sim.yaml")
assert report.ok, str(report)
```

Static checks (llm_ready, @op metadata, schema well-formedness, unbounded-
numeric-write warnings) plus live probes on the sim (limits actually reject
pre-I/O, writes actually hit the audit log, capabilities actually isinstance).

## Make it discoverable to an authoring agent (optional)

`shal.catalog()` lets an LLM read every registered driver/bus and construct valid
YAML. It **derives** what it can — `compatible`, required parent `kind`, the
capability Protocol, ops + `@shal.op` annotations, the docstring summary. You only
declare the irreducible bit: the **address grammar as a JSON-Schema fragment** (and a
`config_schema`) via an optional `authoring_meta()` classmethod:

```python
@classmethod
def authoring_meta(cls) -> dict:
    return {
        "address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                           "description": "7-bit I2C address", "examples": [72]},
        "config_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
```

`@shal.op` side-effects map to MCP-style annotations (`readOnlyHint` /
`idempotentHint` / `destructiveHint`) automatically. A driver without
`authoring_meta()` still appears in the catalog — just without an address example.

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

- A sim model so the driver runs with zero hardware — register it for the bus
  family that matches your `kind`: `@sim_model` (`shal,sim-i2c`, ByteTransport),
  `@scpi_sim_model` (`shal,sim-scpi`, SCPI dialect), or `@msg_sim_model`
  (`shal,sim-msg`, any MessageTransport dialect).
- One test per capability op against the sim, checking VALUES (decode math —
  use the documentation's worked examples as test vectors).
- Retry behavior: idempotent op survives one `fail_next`; non-idempotent op
  propagates `delivered="unknown"` untouched.
- One `LimitError` test per declared bound (call past it; sim state unchanged).
- `isinstance(driver, <CapabilityProtocol>)` — the runtime_checkable contract.
- Load-time: wrong parent kind fails with a clear LoadError.

Generating a driver from device documentation with no human-written code?
Follow [shal-generate-driver](../shal-generate-driver/SKILL.md) — the doc→
driver recipe built on this contract plus [docs/SDK.md](../../../docs/SDK.md).
