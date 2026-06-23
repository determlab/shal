---
name: shal-build-bus
description: Implement a new SHAL bus family (a transport hop - serial, mqtt, CAN, cloud APIs, container exec...). Use when a device must be reached over a link no bundled bus covers, or when reviewing/fixing a bus implementation.
---

# Build a SHAL bus

A bus is a `Driver` that is also a `Transport` and implements one or more
**transport kinds**. Complexity flows toward bus authors so users and driver
authors stay simple — read this whole checklist before writing code.

Pick the bus's `compatible` id and target domain library (`buses/embedded`,
`buses/net`, `buses/fieldbus`, …) from [docs/CATALOG.md](../../../../docs/CATALOG.md)
— claim it there so two authors don't collide.

## Skeleton

```python
from shal import Driver, HopError, LoadError, MessageTransport, Transport, register
from shal.log import bus_logger, current_txn
from shal.node import Node

@register                                  # or via the shal.drivers entry point
class MyBus(Driver, Transport, MessageTransport):
    compatible = "vendor,my-bus"
    kind = None                            # or the kind the PARENT must provide
                                           # (e.g. CommandTransport when this bus
                                           # renders onto an upstream hop)

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)     # the ONLY base with state
        # parse/validate node.address NOW -> raise LoadError (fail at load)
        self.log = bus_logger("my_bus", node.path)
```

## The contract, point by point

1. **Pick kinds honestly.** `ByteTransport.txn(addr, ops)->bytes`,
   `CommandTransport.run(argv, stdin)->Completed`,
   `MessageTransport.exchange(addr, msg)->Mapping`, and `Stream.subscribe(addr,
   topic)->ChannelHandle` (held async push — Phase 2). Validation uses `kinds()`
   (an isinstance sweep) — never add a method you don't fully implement.
2. **No shell strings. Ever.** A `CommandTransport` carries argv vectors. A bus
   that renders onto an upstream `CommandTransport` builds a `list[str]`
   (see `i2c_cli.py` — the canonical example) and calls `self.upstream.run(argv)`.
3. **Address grammar**: implement `validate_address(addr)` raising `LoadError`
   for malformed CHILD addresses, and validate the bus's OWN address in
   `__init__`. Hostile values must never reach transport code at runtime.
4. **Lifecycle is lazy.** Do nothing in `__init__` but parse + validate.
   Connect in `activate()`; `ensure_ready()` (inherited) handles the cache.
   `is_active()` must be a cheap LOCAL check, never a round-trip.
   `close()` drops the connection AND any session state so reconnect re-logs-in.
5. **Locking**: every public transport method body is
   `with self.lock: self.ensure_ready(); ...` — check → activate → talk,
   atomically. Never call into a DIFFERENT bus's lock except via `upstream`.
6. **Error mapping is the heart of the retry policy.** Wrap every failure in
   `HopError(msg, path=self.host.path, hop="my-bus", txn=current_txn.get(),
   delivered=...)`:
   - `delivered="no"` — failure certainly BEFORE the request reached the device
     (connection refused, not connected, local exec missing).
   - `delivered="unknown"` — anything after send (timeout, dropped reply,
     HTTP error response). The framework auto-retries ONLY idempotent ops and
     ONLY on `delivered="no"`. Misreporting "unknown" as "no" causes double
     side effects — when unsure, say "unknown".
   - Chain with `raise ... from e`; timeouts use `HopTimeout`.
7. **Security defaults**: network buses are encrypted by default; plaintext is
   a per-node `insecure: true` opt-out checked at load. Secrets come from
   `config:`/env (see the loader), never logged — error texts must not contain
   query strings, tokens, or passwords.
8. **Logging** (`self.log`, kwargs become structured fields):
   - INFO lifecycle: `connect`/`close` with `event=` and `duration_ms=`.
   - DEBUG hop traces: one record per txn/run/exchange, `event="txn"|"run"|
     "exchange"`, payloads only via `shal.log.redact()`.
   - Raise OR log — never both at ERROR.
9. **Per-child buses** (mux-style): the driver implements
   `provide_child_bus(child) -> Transport`; per-mux shared state lives in one
   state object guarded by one lock — never on the parent bus.
10. **A bus is not an agent tool.** Transport methods (`txn`/`run`/`exchange`/
   `subscribe`) are framework plumbing: they are in `Driver._PLUMBING`, so they
   are NOT wrapped, audited, or emitted by `hal.tool_schemas()`. Buses provide
   transport, not capabilities — keep device semantics (and any `@shal.op`) out
   of a bus. The agent only ever sees the *driver* capabilities above the bus.

## Make it discoverable to an authoring agent (optional)

`shal.catalog()` derives a bus's `compatible`, required parent `kind`, and
`provides_kinds` from the class. Declare the irreducible bits via an optional
`authoring_meta()` classmethod — the bus's own `address_schema`, a
`child_address_schema` (the grammar for addresses on the bus it provides), and a
`config_schema`, all as JSON-Schema fragments:

```python
@classmethod
def authoring_meta(cls) -> dict:
    return {
        "address_schema": {"type": "string", "pattern": r"^/dev/i2c-\d+$",
                           "examples": ["/dev/i2c-1"]},
        "child_address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                                 "examples": [72]},
        "config_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    }
```

## Registration

Explicit `@shal.register` works in-process (tests, playground). A published
package adds the entry point:

```toml
[project.entry-points."shal.drivers"]
"vendor,my-bus" = "my_pkg.my_bus:MyBus"
```

## Tests to write (conformance minimum)

- Address grammar: bad bus address and bad child address each fail the LOAD.
- One happy-path roundtrip per kind (use a local fake server/exe — see
  `tests/test_buses.py` `_Echo` and the i2ctransfer shim).
- `delivered="no"` vs `delivered="unknown"` paths each verified.
- Connection caching: two ops, one connect.
- `kinds()` reports exactly what is implemented.
- TLS/insecure rule if network-facing.
