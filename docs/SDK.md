# SHAL Driver & Bus SDK

The complete authoring contract. **Everything you need to write a working
driver or bus is on this page plus the step-by-step skills**
(`.claude/skills/shal-build-driver`, `shal-build-bus`, `shal-build-yaml`,
`shal-generate-driver`). You never need to read SHAL's source — if you do,
that's a bug in this guide: report it.

## Mental model (30 seconds)

A topology is a tree in YAML. **A bus is just a node that provides a transport
to its children.** A **driver** is the class bound to a node by its
`compatible` id ("vendor,part"); it talks through the parent bus's transport
kind and exposes **capabilities** — typed methods user code and AI agents call.
The framework owns everything that isn't device knowledge: retries, limits
enforcement, logging, audit, the agent tool surface.

```
YAML node ──binds──▶ Driver ──calls──▶ self.bus.<kind method> ──▶ parent bus ──▶ wire
                       │
                       └─ capability methods ──▶ hal.get_device(...) / agent tools
```

---

## 1. Driver anatomy

```python
import shal
from shal import Driver, idempotent, op
from shal.transport import ByteTransport, Read, Write   # for I2C/SPI drivers

@shal.register                       # in-process; published packages use entry points
class Sht31(Driver, shal.TemperatureSensor):
    compatible = "sensirion,sht31"   # lowercase "vendor,part" — the binding key
    kind = ByteTransport             # transport kind the PARENT bus must provide
    llm_ready = True                 # REQUIRED for device drivers: enforces @op
                                     # metadata on every public op at load time

    @idempotent                      # a read: safe for the framework to auto-retry
    @op("Read the ambient temperature now.", unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x24\x00"), Read(6)])
        return -45 + 175 * ((raw[0] << 8) | raw[1]) / 65535

    @classmethod
    def authoring_meta(cls) -> dict:           # powers shal.catalog()
        return {"address_schema": {"type": "integer", "minimum": 3, "maximum": 119,
                                   "description": "7-bit I2C address", "examples": [68]},
                "config_schema": {"type": "object", "properties": {},
                                  "additionalProperties": False}}
```

**Framework-injected attributes** (available after bind, i.e. in every method):

| Attr | What |
|---|---|
| `self.bus` | the parent bus (call its transport-kind method) |
| `self.addr` | this node's `address:` from the YAML |
| `self.node` | the topology node (`.path`, `.id`, `.spec`) |
| `self.log` | structured logger (`self.log.debug("msg", event="...")`) |

**Class attributes you set:** `compatible` (required), `kind` (required unless
the driver sits at root), `llm_ready = True` (required for device drivers).

**Override `bind(self, node)`** (call `super().bind(node)` first) only when you
must parse the address once — e.g. `self.ch = int(node.address)`. Raise
`shal.LoadError` with the node path for a malformed address.

**Public method = capability op.** Every public method is wrapped by the
framework (txn id, retry policy, audit, limits, tool surface). Prefix helpers
with `_` to keep them private. ~40–80 lines is a normal driver; >200 means
you're doing the framework's job.

## 1b. Reads must be live — a value, or a raise (never a stale default)

A read's return value is a **promise that the device answered this call**. SHAL's
whole value is "trust what the agent reads," so:

> **A read returns a value only if the device actually responded. Otherwise it
> raises `shal.HopError` — never a cached, seeded, or default value dressed up as live.**

For a bus-based driver this is automatic: `self.bus.txn(...)` raises on a transport
failure, so you only reach the parse when real bytes came back. The trap is
**wrapping a third-party library** (a cloud / async client) that returns a *default*
before it has heard from the device — hand that back and an agent will trust a number
the device never sent. Guard it:

```python
@idempotent
@op("Read the battery percent.", unit="percent", side_effect="none")
def read_battery_percent(self) -> int:
    resp = self._client.request(GetBattery())          # your library call
    if resp is None or not getattr(resp, "fresh", True):   # no live answer this call
        raise shal.HopError("battery: no response from device",
                            path=self.node.path, hop="cloud", delivered="unknown")
    return int(resp.value)
```

Rule of thumb: if you can't point to *this call's* response, **raise** — don't return a
guess. SHAL enforces this for its own buses; for a wrapped library only you can —
`conformance.check_driver()` cannot see inside someone else's client. (#53, ARCHITECTURE D12.)

## 2. Capabilities

Code depends on capabilities, never on driver classes. **Use a blessed protocol
when one fits** (implement every method, exact names/signatures/units):

| Protocol | Methods |
|---|---|
| `TemperatureSensor` | `read_celsius() -> float` |
| `PowerMonitor` | `read_voltage() -> float`, `read_current() -> float`, `read_power() -> float` |
| `PowerSupply` | `set_voltage(volts: float) -> None`, `read_voltage() -> float`, `read_current() -> float`, `output(on: bool) -> None` |
| `DigitalMultimeter` | `measure_voltage_dc() -> float`, `measure_current_dc() -> float`, `measure_resistance() -> float` |
| `ADC` | `read_voltage(channel: int = 0) -> float` |
| `GPIOExpander` | `set_direction(pin: int, output: bool) -> None`, `write_pin(pin: int, high: bool) -> None`, `read_pin(pin: int) -> bool` |

All are imported from `shal` (e.g. `shal.PowerSupply`). Units are baked into
names: celsius, volts, amperes, watts, ohms — never return raw counts.

**No blessed protocol fits?** Define a driver-local one in your module and
implement it — community capabilities graduate into core later:

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class HumiditySensor(Protocol):
    """v0.1.0 — relative humidity, percent (0-100)."""
    def read_humidity_percent(self) -> float: ...
```

**Actuators: the `safe_state()` hook.** A driver that drives motion may override
`def safe_state(self) -> None` to command its device to a known-safe resting state
(stop, de-energize, retract). It is a no-op on `Driver` today; the Phase-2 actuator
watchdog will call it on disconnect/timeout. Define it now for any actuator so the
driver is watchdog-ready (the call is framework-owned — never invoke it yourself).

## 3. Talking to the bus: the three transport kinds

Your `kind` declares which ONE of these the parent bus must provide; `self.bus`
implements it. Payload contents are yours (you know your device); the *shape*
is fixed.

### `ByteTransport` — I2C / SPI / addressed byte transactions

```python
raw: bytes = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
```
`Op = Write(data: bytes) | Read(n: int)`; a sequence expresses repeated-start
write-then-read. `raw` concatenates all `Read` results in order.

### `CommandTransport` — run a program (local, over SSH, in a container)

```python
out = self.bus.run(["i2ctransfer", "-y", "1", "r2@0x48"], stdin=b"")
out.stdout, out.stderr, out.exit     # Completed(bytes, bytes, int)
```
**argv vectors only, never shell strings** — build a `list[str]`. A non-zero
exit is yours to interpret; raise `shal.HopError` (see §5).

### `MessageTransport` — structured request/response (HTTP, cloud, SCPI, sim)

```python
reply: dict = self.bus.exchange(self.addr, {...message...})
```
Message dialects by bus family (a driver written for one dialect works on every
bus speaking it, including its sim twin):

| Bus family | You send | You get back |
|---|---|---|
| `shal,http` (+ sims) | any JSON-able dict — POSTed to `<base>/<addr>` | the JSON reply dict |
| `shal,scpi-raw` / `shal,sim-scpi` | `{"scpi": ":MEAS:VOLT? CH1", "query": True}` (omit `query` for writes) | `{"reply": "3.2999"}` (`""` for writes) |
| `shal,sim-msg` | any dict (your protocol) | whatever the sim model returns |

**Units** are free strings — celsius/volts/amperes/watts/ohms are conventions,
not a closed set; use the device's unit (`percent`, `pascal`, `lux`, …).

## 3b. Using a loaded topology (the runtime API)

`shal.load("topology.yaml")` returns a `Hal` (a context manager — use `with`).
Everything is **lazy**: nothing connects until the first capability call, so
there is **no `activate()`** and you never open hops yourself.

```python
import shal
with shal.load("tests/sim.yaml") as hal:
    dev = hal.get_device("dut")          # by id (or "/path/to/node", or path=...)
    dev.read_celsius()                   # first call opens the path on demand
    # agent surface for the same tree:
    hal.tool_schemas(); hal.tool_catalog(); hal.call_tool("dut__read_celsius", {})
```

| `Hal` method | Returns |
|---|---|
| `get_device(id_or_path)` | the bound **driver** (or the **bus** object if the node is a bus — that's how tests reach sim hooks like `fail_next` / `model_for`) |
| `get_node(id)` | the topology `Node` (`.path`, `.id`, `.driver`, `.spec`) |
| `close()` | teardown leaf→root (the `with` block does this for you) |

In tests, reach a sim bus's hooks via the bus node:
`bus = hal.get_node("bench").driver; bus.fail_next = 1; bus.model_for(addr).temp_c = 30`.

## 4. Operating limits (declare once — advertised AND enforced)

If the documentation states a safe operating range for a write parameter,
**declare it**. One declaration gives you: the constraint advertised to agents
in the tool schema, framework enforcement *before any I/O* (`shal.LimitError` —
the device never sees an out-of-range command), a `catalog()` manifest entry,
and an audit record for rejected attempts. Your method body stays check-free.

```python
@op("Set this channel's output voltage (absolute setpoint).",
    unit="volt", side_effect="write",
    params={"volts": {"minimum": 0.0, "maximum": 32.0}})   # from the datasheet
def set_voltage(self, volts: float) -> None:
    self.bus.exchange(self.addr, {"scpi": f":SOUR{self.ch}:VOLT {volts}"})
```

- `params` maps a parameter name → JSON-Schema fragment. Allowed keywords:
  `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`, `enum`,
  `const`, `multipleOf`, `type`, `description`, `examples`.
- `enum` covers mode strings: `params={"mode": {"enum": ["VOLT", "CURR"]}}`.
- **Address-dependent ratings** (e.g. PSU channel 3 is the 5 V rail) — override
  the hook; it may only *tighten*:

```python
def op_limits(self):
    return {"set_voltage": {"volts": {"maximum": 5.3}}} if self.ch == 3 else {}
```

- **Installation policy** belongs in YAML, not your driver — a rig owner caps
  any limited op per node (also tighten-only; widening fails the load):

```yaml
config:
  limits:
    set_voltage:
      volts: {maximum: 5.0}
```

What's NOT expressible declaratively (cross-parameter envelopes like V×I ≤ W,
state-dependent rules): check imperatively in the method body and raise a
`shal.Error` subclass — and say so in the op description.

## 4b. Actuation approval — "ask before it moves" (issue #14)

Mark an op that causes **physical motion** `side_effect="actuator"`, or a
**destructive / configuration** write (factory-reset, erase, firmware push)
`side_effect="config"`. The framework then consults the active `Approver`
*after* the limit check and *before* any bus I/O — so an impossible call is
rejected by limits without ever asking, and an approved call is the only thing
that reaches the device.

```python
@op("Send the robot out cleaning.", side_effect="actuator")
def start_cleaning(self) -> None:
    self.bus.exchange(self.addr, {"cmd": "clean", "data": {"act": "start"}})
```

- **You write nothing else.** No prompt, no flag, no check in the body — the
  wrapper owns the gate, exactly like limits and audit. It fires on *both* call
  paths (`call_tool` and raw `get_device().method()`); there is no bypass.
- **The host supplies the decision**, not the driver. SHAL ships `AutoApprove`,
  `DenyAll`, `CallableApprover`, and the default `ConsoleApprover` (prompts when
  interactive, denies when headless). Install one:

  ```python
  shal.set_approver(shal.AutoApprove())          # sim / CI / tests
  with shal.approver(MyAgentApprover()): ...      # scoped policy
  ```

- A denied call raises `shal.ApprovalDenied` (nothing sent, like `LimitError`);
  `call_tool` returns `{"ok": False, "rejected": "approval"}`. Every decision is
  written to `shal.audit` (`outcome` = `approved` | `denied`).
- Use `actuator` for motion/dispense and `config` for destructive/configuration
  writes — anything you'd want a human to confirm. Plain `write` (a register, a
  setpoint) is audited but **not** gated, so a benign write never prompts.

## 5. Errors & the retry contract (memorize this)

- **Never catch transport errors. Never retry anything yourself.** The
  framework retries `@idempotent` ops once on `delivered="no"`; a
  delivery-unknown write is surfaced to the *user* — re-firing it is the bug
  SHAL exists to prevent.
- `@idempotent` only on ops safe to run twice (reads; absolute setpoints
  re-asserted). Relative moves / counters / toggles: never.
- **A write/actuator op you want audited must NOT be `@idempotent`.** The audit
  trail records state-changing *commands*, and the audit fires only for
  **non-idempotent** ops (so a retried read isn't logged as a command). So even
  an absolute setpoint like `set_voltage` — although technically retry-safe —
  is left **unmarked** when it has `side_effect="write"`, so it lands in the
  audit log. The conformance kit enforces this (a write op that produces no
  audit record fails). Rule of thumb: mark reads `@idempotent`; leave
  writes/actuators unmarked.
- **Device-said-no ≠ transport failure.** If the transport succeeded but the
  device returned an error code, raise your own `shal.Error` subclass — the
  retry machinery must not see it.
- Buses (not drivers) raise `HopError(msg, path=..., hop=..., txn=..., delivered=...)`
  (`txn` optional — pass the current transaction id when you have it):
  `delivered="no"` = certainly not delivered (refused/never sent); `"unknown"` =
  anything after send. Unsure → `"unknown"`.
- `shal.LimitError` is raised by the framework, never by you.

## 6. Sims — prove it with zero hardware

Every driver ships with a sim model so it (and its tests) run with no device.
Register a model for your `compatible` against the matching sim bus:

| Your kind | Sim bus | Register | Model interface |
|---|---|---|---|
| `ByteTransport` | `shal,sim-i2c` | `from shal.buses.sim import sim_model` → `@sim_model("vendor,part")` | `txn(self, ops) -> bytes` (iterate `Write`/`Read` ops, keep register state) |
| `MessageTransport` (SCPI dialect) | `shal,sim-scpi` | `from shal.buses.sim_scpi import scpi_sim_model` → `@scpi_sim_model("vendor,part")` | `scpi(self, cmd: str) -> str` (return `""` for writes) |
| `MessageTransport` (any dialect) | `shal,sim-msg` | `from shal.buses.sim_msg import msg_sim_model` → `@msg_sim_model("vendor,part")` | `handle(self, msg) -> Mapping` |

Sim buses build one model instance per child node at activation and offer test
hooks: `fail_next = N` (next N calls drop with `delivered="no"` — exercises the
retry path) and `fail_delivered_unknown = True` (one ambiguous failure).
`bus.model_for(addr)` returns the model so tests can set internal state.

Test topology pattern:

```yaml
shal_version: 1
root:
  bench:
    id: bench
    driver: shal,sim-i2c        # or sim-scpi / sim-msg
    address: sim0
    children:
      dev: {id: dev, driver: "vendor,part", address: 0x44}
```

## 7. Conformance — the definition of done

```python
from shal import conformance
report = conformance.check_driver("vendor,part", topology="tests/sim.yaml")
assert report.ok, str(report)
```

Verifies: `llm_ready` + complete `@op` metadata, catalog entry + all schemas
well-formed, declared limits **actually reject** out-of-range calls pre-I/O,
write ops **actually produce audit records**, capability protocols actually
`isinstance`. Warnings flag numeric write params with no declared limit.
A generated driver is not done until this is green.

**Your tests must additionally cover** (with the sim): one value-correctness
test per op using *worked examples from the device documentation* (datasheet
conversion examples are test vectors!), the retry behavior (`fail_next=1` →
idempotent op recovers; `fail_delivered_unknown` → raises with
`delivered="unknown"`), and limit rejection for each declared bound.

## 8. Registration & packaging

In-process (tests, examples, generated artifacts): `@shal.register` on the
class — importing the module registers it. Published packages add:

```toml
[project.entry-points."shal.drivers"]
"vendor,part" = "my_pkg.module:ClassName"
```

Two different classes claiming one `compatible` fail the load loudly;
topologies disambiguate with `from: <distribution>`.

## 9. Buses in one box (full guide: `shal-build-bus` skill)

A bus = `Driver` + `Transport` + one or more kind mixins. `Transport.__init__`
is the only stateful base. Parse + validate your own address in `__init__`
(`LoadError`), validate child addresses in `validate_address()`. Lifecycle is
lazy: connect in `activate()`, `is_active()` is a cheap local check, `close()`
drops connection AND session state. Every public transport method body:
`with self.lock: self.ensure_ready(); ...`. Map failures to `HopError` with
honest `delivered`. Network buses: TLS by default, plaintext requires the node
to declare `insecure: true` (check in `__init__`). Never shell strings.
Secrets come from `config:`/env and never appear in logs or error text.

A bus's `__init__` takes the `node` and calls `Transport.__init__(self, node)`,
which gives you these attributes:

| Attr | What |
|---|---|
| `self.host` | the bus's own node (`self.host.path`, `self.host.address`) |
| `self.lock` | the per-bus `RLock` — wrap every transport method body |
| `self._active` | connection-state flag (`is_active()` reads it; flip it in `activate()`/`close()`) |
| `self.upstream` | the parent bus when this bus is nested (e.g. renders argv onto a `CommandTransport`); `None` at root |

Config/secrets: read `node.spec.get("config", {})` in `__init__` — the loader
has already resolved any `${ENV_VAR}` references. A leaf network bus (opens its
own socket) sets `kind = None`; a bus that renders onto a parent sets `kind` to
the parent kind it needs.

## 10. The agent surface (what your metadata becomes)

`hal.tool_schemas()` turns every op into an LLM tool: your `@op` description +
the node's YAML `description:` + the merged `input_schema` (with your limits as
schema constraints). `hal.tool_catalog()` adds `side_effect`/`idempotent`/MCP
hints for gating. `hal.call_tool(name, args)` dispatches; a limit violation
returns `{"ok": False, "rejected": "limits", "violations": [...]}` — nothing
was sent. `shal.catalog("vendor,part")` is the authoring manifest: ops, units,
schemas, your `authoring_meta`. Write descriptions that say **when** to call
the op, not just what it does.

## 11. Don'ts

- Don't read SHAL source — this page + skills are the contract.
- Don't catch/retry transport errors; don't sleep/poll around failures.
- Don't validate limit params in the body (declare in `params=`); DO validate
  cross-parameter envelopes imperatively.
- Don't print; don't configure logging; use `self.log`.
- Don't add dependencies. Core is stdlib + pyyaml + jsonschema.
- Don't put device state in class attributes (instances per node share them) —
  except deliberate test hooks.
- Don't invent capability method names when a blessed protocol exists.
