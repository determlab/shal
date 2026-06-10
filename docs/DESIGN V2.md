# SHAL — System/Software Hardware Abstraction Layer

A framework for describing and controlling a HW and/or SW setup — a server wired to
eval boards over I2C, a home robot reached over WiFi, or any mix — from Python.
Inspired by the Linux device tree, but dynamic, user-space, and network-capable.

**Doc revision: v2** (2026-06-10). Incorporates the design review: hardened transport
contract (typed kinds, argv — no shell strings), retry/idempotency policy, mux fixes,
concurrency model, security section, lifecycle, actuator safety, and a new
**Logging & observability** section. Deltas from v1 listed at the end.

## Design principle (governs everything below)

SHAL has three audiences: **end users** (write YAML, call capabilities), **driver
authors** (the community), and **bus authors** (rare experts + core). **Complexity
flows toward the rarest audience; simplicity flows toward users.** Any proposal that
pushes complexity toward end users is rejected — including future revisions of this doc.

## Core idea

A **bus is just a node that provides a transport to its children.** Every link —
I2C, SPI, TCP/WiFi, SSH, USB, in-process — implements the same transport contract.
This makes the tree recursive: "SSH to a server that has an I2C controller with 4
boards" has the same shape as "WiFi to a robot with an internal SPI sensor."

Layering: **topology (declarative) → driver binding → capability API (what code calls).**

## Entities

- **Node** — anything in the tree (device, board, bus controller). Has an address
  within its parent, an optional `id`, and an auto-derived path.
- **Bus** — a node whose driver implements at least one **transport kind** (see
  decision 4), exposing it to the node's children. Buses can nest arbitrarily.
- **Driver** — code bound to a node by a `compatible` id. Talks to the device using
  the *parent bus's* transport kind and implements typed capability Protocols.
- **Capability** — the contract user code calls: `TemperatureSensor.read_celsius()`,
  `Motor.set_speed()`. Abstract, decoupled from how the device is reached.
  **Inferred from the driver** (which Protocols it implements) — never declared in YAML.

## Key decisions (locked)

1. **Description format** — declarative topology in **YAML** + drivers/buses in
   **Python**. Lowest barrier to adoption (the priority for a standard).
   - Topology files begin with `shal_version: 1`; the format has a published JSON
     Schema and evolves by version. Formats that can't evolve die; ones that evolve
     unversioned corrupt.
   - **Invariant:** the loader uses `yaml.safe_load` only. Anything that constructs
     arbitrary Python objects from the topology file is forbidden.

2. **Addressing** — path scheme `/server/i2c0/board2/temp0` across bus types. Each bus
   family defines what an address means (I2C → `0x48`, TCP → host:port, SSH →
   user@host) **and publishes an address grammar, validated at load** (7-bit I2C is
   `0x03–0x77`, full stop). A malformed or hostile address never reaches transport
   code. Generalized `reg`.

3. **Driver binding** — registry keyed by a `compatible` string (`"ti,tmp102"`).
   Node declares what it is; framework finds the driver. Community extends by
   publishing packages, not editing core.
   - Discovery via Python **entry points** (the pytest-plugin pattern): drivers run
     because they were *installed on purpose*.
   - A topology referencing an uninstalled `compatible` **fails the load**. The
     framework never imports a module named by a config string.
   - **One `driver:` key for every node.** Bus-ness comes from the driver's transport
     kinds, not from a separate `bus:` key. (Supersedes the v1 `bus:`/`driver:` split.)

4. **Bus ↔ driver contract — typed transport kinds.** *(Replaces v1's single untyped
   `exchange(Any) -> Any`.)* A bounded set of kinds; **the kind, not the individual
   bus, owns the payload type.** Contents stay opaque within a kind; the *shape* is
   typed and mypy-checkable. See "Transport kinds" for the contract.

5. **`id` field** — optional, **globally unique** (validated at load, fail loudly on
   dupes), location-independent handle. Path = location; id = stable semantic name.
   Moving a device changes its path, not its id. `$ref`s target **ids**.

6. **Retry & side effects** — reads and ops marked idempotent auto-retry across
   transient drops; **a write is never silently re-fired.** See "Retry & idempotency".

7. **Logging discipline** — SHAL is a library: it emits structured records and never
   configures logging. See "Logging & observability".

## Lookup API

```python
import shal

with shal.load("setup.yaml") as hal:          # context manager guarantees teardown
    dev = hal.get_device(id="ambient_temp")   # by stable id (common case)
    dev = hal.get_device(path="/server/i2c0/temp0")  # by topology path
    dev.read_celsius()                        # capability call, transport-agnostic
```

Bare `hal = shal.load(...)` also works (REPL/notebook use); cleanup is then
best-effort at GC/exit. The `with` form is the documented default.

## Canonical examples

### 1 — Remote sensor, nothing installed on the far side

```yaml
shal_version: 1

root:
  lab_server:
    id: lab_server
    driver: shal,ssh-host
    address: ${SHAL_LAB_SSH}        # secret lives in env / secrets backend, not in git
    children:
      i2c0:
        driver: shal,i2c-cli        # I2C rendered as argv over ssh; far side needs only i2c-tools
        address: /dev/i2c-1
        children:
          temp0:
            id: ambient_temp
            driver: ti,tmp102
            address: 0x48
```

```python
with shal.load("setup.yaml") as hal:
    print(hal.get_device(id="ambient_temp").read_celsius())
```

### 2 — Test rack: mux + failover (the one place policy is visible)

```yaml
shal_version: 1

root:
  rack_a:
    id: rack_a
    driver: shal,ssh-host
    address: ${SHAL_RACK_A}
    children:
      i2c0:
        driver: shal,i2c-cli
        address: /dev/i2c-1
        children:
          mux0:
            driver: nxp,pca9548          # a node that is also a bus
            address: 0x70
            children:
              ch0:
                address: 0               # channel node: address only, no driver
                children:
                  dut_a_temp: { id: dut_a_temp, driver: ti,tmp102, address: 0x48 }
              ch1:
                address: 1
                children:
                  dut_b_dac:  { id: dut_b_dac, driver: microchip,mcp4725, address: 0x60 }
  rack_b:
    id: rack_b
    driver: shal,ssh-host
    address: ${SHAL_RACK_B}
    children:
      i2c1: { driver: shal,i2c-cli, address: /dev/i2c-1 }

  chamber_temp:
    id: chamber_temp
    driver: ti,tmp102
    routes:                              # failover: explicit, ordered, per-route address
      - { via: /rack_a/i2c0, address: 0x4a }
      - { via: /rack_b/i2c1, address: 0x49 }
```

```python
try:
    dac.set_voltage(1.25)            # a write is NEVER silently re-fired
except shal.HopError as e:           # "...connection lost after send (hop: ssh)" — delivery unknown
    dac.set_voltage(1.25)            # the USER decides the re-send is safe
```

### 3 — Robot: streaming, watchdog, secured network bus

```yaml
shal_version: 1

root:
  home_net:
    driver: shal,mqtt
    address: ${SHAL_MQTT_BROKER}     # mqtts:// — TLS by default; plaintext requires `insecure: true`
    children:
      cleaner:
        id: cleaner
        driver: myrobot,cleaner-v2
        address: robots/cleaner      # an "address" is whatever the bus family says
        watchdog_ms: 300             # no command for 300 ms → driver's safe_state()
```

```python
bot = hal.get_device(id="cleaner")
bot.start_cleaning()                          # sync, no event loop in sight
for ev in bot.events("status"):               # blocking iterator (scripts/notebooks)
    if isinstance(ev, shal.Gap):              # drop surfaced, never hidden
        continue
    if ev.kind == "dustbin_full":
        bot.dock(); break

# asyncio flavor of the same device:
#   async for pose in bot.stream("pose"): ...
```

## Propagation (recursive parent-bus model)

Each node holds the bus instance that connects it to its parent; each bus knows the
node it lives on. **One relation, used everywhere** (resolves the v1 `host` /
`parent_bus` drift):

```python
class Node:
    parent_bus: Bus | None            # bus exposed by the parent node; None at root

class Bus(Transport):
    host: Node                        # the node this bus lives on
    @property
    def upstream(self) -> Bus | None:
        return self.host.parent_bus   # the only way up
```

A call unwinds **up** the tree: each hop encodes its operation for its kind and hands
it to `upstream`. Base case = root (`upstream is None`), which executes locally and
stops the recursion.

## Cycles: tree + references

Tree stays the readable backbone. A back-edge (a PC that SSHes back to the server) is
a **reference to an existing id**, not a nested child — device-tree phandle style.

```yaml
back_link:
  driver: shal,ssh-host
  to: $lab_server        # $<id>; loader links instead of recursing, so load terminates
```

A `$ref` is a name pointer, **not** a second parent — you never route through it.
**Rule:** every tree walk carries a visited-set guard.

## Muxes

A mux is a node that is also a bus. One `MuxChannel` bus instance per channel; **all
channels of one physical mux share one per-mux state object** — the cache does NOT
live on the parent bus (v1 bug: two muxes on one upstream bus stomped a shared
`current_channel` field and silently mis-routed).

```python
class MuxState:
    selected: int | None = None
    lock: RLock                        # guards check → select → talk for this mux

class MuxChannel(Bus):
    state: MuxState                    # shared across this mux's channels only
    channel: int
    mux_addr: int

    def is_active(self) -> bool:
        return self.state.selected == self.channel
    def activate(self) -> None:
        self.upstream.txn(self.mux_addr, select(self.channel))
        self.state.selected = self.channel
```

- **Transparent forwarding is explicit per-kind delegation** that selects *inside the
  call*, under `state.lock`. No `__getattr__` magic: attribute access must be
  side-effect-free (v1's sketch fired a hardware select on `hasattr`/`repr`/pickling
  and selected at lookup time, outside the lock).
- Repeat access to the same channel pays nothing; switching re-selects.
- **Cache validity:** trusted only if SHAL is the sole bus owner. Per-bus
  `verify: true` re-reads/re-asserts real state for shared/untrusted buses.
- **Streams through a mux:** a subscription **pins its channel** for its lifetime.
  Sync access to a sibling channel while pinned raises `shal.Busy` naming the holding
  subscription — fail loudly, don't silently yank a held stream's channel.
  (Alternative considered — reject subscribe-through-mux at setup — rejected:
  pinning is explicit and genuinely useful.)

## Remote / multi-hop: the crossing is just another bus

A remote hop is **not a special core mechanism** — it's a bus the user picks per link.
Interchangeable remote buses, all `CommandTransport` and/or `MessageTransport`
(+ `Stream` where they can hold a channel):

- `ssh` — run argv on the far side (default; bringup, labs, eval boards, hobby
  robots). **No on-device software** beyond the CLI tools used.
- `agent` — a tiny SHAL on the far side receives ops natively (high throughput,
  avoids per-call shell cost).
- `rpc` / `container` — later, same interface.

Swapping `driver: shal,ssh-host` → `driver: shal,agent` changes nothing else.
"No-agent" is a **choice**, not a law.

### Default: ssh renders to argv — never to a shell string

The ssh bus contract is **argv vector + stdin → (stdout, stderr, exit)**, executed
without a shell (`ssh host -- prog arg…`, no `sh -c`). A lower bus renders its
operation as argv; the ssh bus carries it. Far-side I2C becomes "I2C-over-CLI":

```python
class I2cCliBus(Bus, ByteTransport):
    busnum: int                                    # derived from address /dev/i2c-1

    def txn(self, addr: int, ops: Sequence[Op]) -> bytes:
        argv = ["i2ctransfer", "-y", str(self.busnum), *render(ops, addr)]
        out = self.upstream.run(argv)              # CommandTransport carries it
        return parse(out.stdout)
```

**Why argv is load-bearing (security):** the moment any driver interpolates a value
that isn't a trusted literal into a shell string, a community-extensible standard has
remote code execution. The safe path must be the only easy path: drivers build lists,
never strings. (A string mode, if ever added for ergonomics, is opt-in, loud, and
quoted by ONE audited core function — never by driver code.)

Costs of the ssh-CLI default: one ssh round-trip per transaction (slow, serial);
remote must have the CLI tool; stateful sequences are fiddlier. When this matters,
swap to `agent` — same interface. A persistent ssh session is kept per connection;
the real cost is per-call shell spawn, not the absence of an agent.

## Path activation (opening muxes + connections)

A path is "open" when every hop is ready: connections open, muxes selected. Handled
implicitly by the recursion, not a separate root→leaf pass.

**Ordering:** opening is **lazy / on-demand**. Everything funnels through `upstream`,
and you physically cannot send a byte to a child without traversing its parent — so
hops open root→leaf even though the code unwinds leaf→root.

**Invariant (bus contract):** every readiness action must be expressed as a message
routed through `upstream`. A bus must NOT ready itself by touching far-side hardware
directly — behind a remote hop that hardware is not local to the caller, so readiness
can only be something the parent carries. *(v1 justified this with "the no-agent
constraint"; no-agent was since repealed — the invariant stands on these grounds.)*

**Efficiency — `is_active()` so we don't re-activate every call:**

```python
class Bus(Transport):
    def ensure_ready(self) -> None:
        if not self.is_active():        # cheap LOCAL check — never a round-trip
            self.activate()
```

- Connection hops: `is_active()` = "socket open and no error observed". This is an
  **optimistic** check, not liveness — a half-open peer (power cut, NAT timeout)
  passes it until a write fails. That's fine **because the retry/idempotency policy
  is the safety net**, not `is_active()`.
- Selection hops (mux): per-mux cached `selected`, as above.

## Transport kinds (the decision-4 contract)

A shared `Transport` base (state + lifecycle) plus **stateless per-kind mixins**.
A bus declares the kinds it implements; **each kind owns its payload type**:

```python
class Transport:                       # the only class with state / __init__
    host: Node
    def ensure_ready(self): ...
    def is_active(self) -> bool: ...
    def activate(self) -> None: ...
    def kinds(self) -> frozenset[type]: ...   # honest introspection — see validation

class ByteTransport:                   # addressed byte transactions (i2c, spi, modbus…)
    def txn(self, addr, ops: Sequence[Op]) -> bytes: ...
    # Op = Write(bytes) | Read(n) — sequences cover repeated-start write-then-read;
    # an I2C payload was never "bare bytes".

class CommandTransport:                # argv execution (ssh, local shell, container…)
    def run(self, argv: Sequence[str], stdin: bytes = b"") -> Completed: ...
    # Completed = (stdout: bytes, stderr: bytes, exit: int). No shell. Ever.

class MessageTransport:                # structured messages (http, mqtt, agent, rpc…)
    def exchange(self, addr, msg: Mapping) -> Mapping: ...

class Stream:                          # async push; see next section
    def subscribe(self, addr, topic) -> Subscription: ...

class MqttBus(Transport, MessageTransport, Stream): ...
```

- **Bounded set** — start with these; add a kind only when a real bus forces it.
- **Contents stay opaque within a kind** (a tmp102 driver knows its own register
  bytes); the **shape** is typed, so mypy and IDEs work for driver authors, and two
  independently-written I2C buses cannot silently disagree on payload shape — the
  interop failure the capability layer prevents one level up must not reopen here.
- Mixins have no `__init__`/state → only `Transport` initializes → no diamond.
- **Binding validation rides on activation:** each hop asserts via `kinds()` that it
  supports the needed kind; first hop that doesn't → **fail loudly at setup**.
  Validation uses `kinds()`, never `hasattr` — forwarding buses delegate explicitly,
  so introspection stays honest and side-effect-free.

## Async / streaming (second primitive)

Sync is leaf→root request/response. Async is the mirror: data originates at a leaf
**unsolicited** and travels root-ward. Second primitive — **explicitly stateful,
opt-in**, and the channel is **held open end-to-end** for the subscription's life.
No lazy/stateless async.

Over ssh this is a long-running streaming command (`tail -f`, a persistent reader) on
the held session — still nothing installed on the far side.

Rules it forces:

1. **Every hop must support a held stream.** Weakest hop decides; `subscribe` fails
   loudly **at setup** via `kinds()`, never at 2 a.m. Sync still works on the same path.
2. **Sync stays lazy & stateless; async is stateful & explicit.** Events propagate up
   by each hop forwarding into its parent's held channel.
3. **Delivery contract is per-subscription.** Natively pub/sub buses (MQTT) demux
   internally; per-stream buses (ssh `tail -f`) hold one channel per subscription.
   `Stream` promises per-subscription delivery either way.
4. **Drops are surfaced, never hidden:** a `shal.Gap` event marks the missed span,
   then failover/re-subscribe per routing rules. Data loss is the user's business.

**User API is sync-first** (an event loop near a bringup script is the worst possible
ergonomics regression):

```python
for ev in dev.events("status"): ...        # blocking iterator — scripts, notebooks
async for ev in dev.stream("pose"): ...    # asyncio — apps; maps 1:1 onto held channels
sub = dev.subscribe(cb); sub.cancel()      # low-level form both are built on
```

Subscription timeouts are two distinct numbers: **setup** (open every hop) and
optional **idle** (no event for N s → Gap or error, per config).

## Capabilities (first-class contracts)

Capabilities are **shared Protocols**, not driver-invented APIs — this is the product.
Code depends on the capability, never the driver.

```python
@runtime_checkable
class TemperatureSensor(Protocol):
    def read_celsius(self) -> float: ...     # unit baked into the name — do this everywhere

class Tmp102(shal.Driver, TemperatureSensor):
    compatible = "ti,tmp102"
    kind = ByteTransport
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return ((raw[0] << 4) | (raw[1] >> 4)) * 0.0625
```

- **Ownership:** a small *blessed core set* (`TemperatureSensor`, `Motor`, `Camera`,
  …), like Linux subsystems. Community capabilities can **graduate** into core via a
  written, lightweight RFC process — define it before the first contributor asks how
  to propose `PressureSensor`.
- **A standard is its semantics, not its signatures:** each core capability specifies
  units, valid ranges, error conditions, and blocking behavior.
- **Versioning:** capabilities carry semver; drivers are verified at load
  (`runtime_checkable` + signature check) to satisfy what they claim. Evolution is
  additive within a major; breaking changes are a new major, negotiated at bind time.
  Without this the standard either ossifies or drifts silently.
- **Inferred, not declared:** capability comes from the driver's Protocols. There is
  no `capability:` key in YAML (v1 examples that carried one are corrected).

### Actuator safety: safe-state on disconnect (first-class requirement)

Software-safe ≠ physically safe. Actuator capabilities (`Motor`, `VacuumRobot`, …)
**mandate deadman semantics**: the driver implements one method, `safe_state()`; the
**framework owns the watchdog plumbing**. A node-level `watchdog_ms: N` means "no
command within N ms → `safe_state()`", engaged by SHAL on connection loss, hop
failure, or command silence. If WiFi drops mid-command the robot stops — by contract,
not by driver folklore. Watchdog trips are logged (WARNING) and surfaced to active
subscriptions.

## Runtime robustness

### Errors — attribute to the failing hop

Failures unwind up through the recursion; each hop wraps with its identity, chained
with `raise … from e` so the original cause is preserved:

```
shal.HopError: /lab/ri2c/rtemp  i2c NAK at 0x48   (hop: ssh → i2c-cli, txn=a3f2)
```

The same `(path, hop, txn)` identity appears in logs and trace spans — one identity
scheme everywhere (see Logging).

### Retry & idempotency (locked decision 6)

The exactly-once problem is real on hardware: if a request reached the device but the
reply was lost, a blind retry **re-executes the side effect** — a motor double-steps,
a register double-increments, and with failover both routes may reach the *same*
physical device.

- **Reads / ops the driver marks idempotent:** retried transparently (reconnect once,
  retry once). The common case stays magic.
- **Writes / unmarked ops:** never silently re-fired. The call raises `HopError`
  carrying `delivered=unknown`; the **user** decides whether re-sending is safe.
- Drivers mark idempotency per capability op (`@shal.idempotent`); the framework, not
  the driver, implements the retry machinery.

### Routing — one path by default; failover opt-in

A node lives at one spot → one route (its single `parent_bus`); zero ambiguity.
Multiple routes exist only if declared; **declared order = priority**; each route
carries its **own address** (`routes: [{via, address}]`); the node's canonical path
derives from the **primary** route.

- On hop error: try the next route; all fail → ONE aggregated error listing each
  route's failure.
- **Sticky:** once connected, stay (no flapping); re-probe primary on reconnect only.
- The overall timeout budget **spans all routes** of one call.
- Async: commit to one route per subscription; on drop, failover = Gap + re-subscribe
  on the next.
- Retried-via-second-route writes obey the idempotency policy above — a
  delivery-unknown write does NOT auto-failover.

### Timeouts — per-hop and overall

Effective hop limit = **min(hop timeout, remaining budget)**. The budget shrinks as
the call descends; the error names which fired: `timeout: i2c hop (0.5 s)` vs
`timeout: overall budget (2 s)`. Subscriptions: setup timeout + optional idle timeout
(previous section).

### Reconnect

- **Sync:** transparent reconnect-and-retry **only** for idempotent ops; otherwise
  raise with delivery state (above).
- **Async:** the drop is surfaced as `Gap` — data may have been missed; the user must
  know, not be lied to. Then failover/re-subscribe.

### Concurrency (the model, not a sentence)

The tree is shared mutable state (connection caches, mux selection); concurrent
access is the norm on a server driving many boards.

- **Topology is immutable after load** → lookups (`get_device`) are lock-free.
- **One RLock per bus instance** — the bus is the shared resource. A mux's channels
  share the per-mux lock (`MuxState.lock`).
- **Acquisition follows the recursion:** each hop acquires its own lock, performs
  check→activate→talk, and calls `upstream` while holding it. All calls therefore
  acquire deeper-locks-before-shallower in the same order → no cyclic wait → no
  deadlock. (`$ref`s are never routed through, so the routing graph stays a tree.)
- Consequence stated honestly: a slow hop (ssh) **serializes** everything behind it;
  that's physics of a shared link, not a bug. The `agent` bus exists for throughput.
- **Stream dispatch:** one reader (thread or task) per subscription; user callbacks /
  iterator consumers must not block dispatch. Consumer code MAY call `exchange` —
  it takes the normal lock path.
- Blocking sync and async coexist on a shared connection hop iff the bus multiplexes
  (ssh exec channels do); otherwise `kinds()`/setup validation rejects the combination.

## Logging & observability

SHAL is a **library**: it emits structured records; the **application** decides
levels, handlers, and destinations. Industry-standard Python discipline, locked:

1. **stdlib `logging`, hierarchical namespaces** mirroring the architecture:
   `shal.loader`, `shal.bus.<family>` (`shal.bus.ssh`, `shal.bus.i2c_cli`),
   `shal.driver.<compatible>`, `shal.route`, `shal.watchdog`, `shal.audit`.
2. **The library never configures logging.** No handlers, no levels, no
   `basicConfig`, no env-var magic in core. Exactly one `NullHandler` on the root
   `shal` logger (the documented stdlib library pattern — silences "no handler
   found" without hijacking app config). The `shal` CLI and the sim harness are
   *applications* and may configure their own.
3. **Raise or log — never both.** Anything surfaced to the caller as an exception is
   not also logged at ERROR (no double reporting). ERROR is reserved for failures
   that *cannot* surface as an exception (e.g. a subscription reader dying in the
   background). WARNING is for anomalies SHAL handled itself.
4. **Level discipline:**
   - `DEBUG` — hop traces: encode/decode, activation decisions, cache hit/miss,
     payloads (redacted, truncated).
   - `INFO` — lifecycle: topology loaded/validated, connection open/close,
     subscription start/stop, failover switch, teardown.
   - `WARNING` — handled anomalies: retry attempt, reconnect, route failover,
     stream `Gap`, mux cache verify mismatch, watchdog `safe_state` engaged.
   - `ERROR` — background-only failures (rule 3).
5. **Structured records.** Every record carries a stable schema of `extra` fields:
   `path`, `hop`, `bus_family`, `addr`, `txn`, `attempt`, `route`, `duration_ms`.
   Message text is for humans; fields are for machines. Any JSON/structlog formatter
   plugs in without SHAL's involvement; an optional `shal.logging.JSONFormatter`
   ships for convenience — never as a dependency.
6. **Correlation.** Every user-level capability call gets a short `txn` id, carried
   through the recursion; all hop records of one call share it, and it appears in
   `HopError`. Grep one id, get the whole multi-hop story. `txn` maps 1:1 onto a
   tracing span.
7. **Redaction by default — enforced by the framework, not by driver diligence**
   (same principle as argv): payload bytes only at DEBUG, hex-encoded, truncated
   (default 64 B, configurable); credentials never — connection records log host,
   never user secrets; env/secret references log the **name** (`${SHAL_LAB_SSH}`),
   never the resolved value. Redaction lives in SHAL's record factory.
8. **The hot path costs nothing when off:** lazy `%`-style formatting only (no
   f-strings into log calls); expensive dumps behind `isEnabledFor(DEBUG)`.
9. **One identity everywhere:** the `(path, hop, txn)` in errors == log extras ==
   span attributes.
10. **Drivers log through `self.log`** — a `LoggerAdapter` pre-bound to
    `shal.driver.<compatible>` with `path`/`id`/`txn` injected. Driver authors write
    `self.log.debug("conversion ready")` and the structured fields come free.

### Audit channel (actuators)

`shal.audit` records every **actuator** capability call at INFO: `txn`, device id,
op name, outcome, duration — args summarized, never dumped. It ships with
`propagate=False` + `NullHandler`, so it is **silent by default** (high-rate motor
loops) and enabled with one line — attach a handler:

```python
logging.getLogger("shal.audit").addHandler(logging.FileHandler("commands.log"))
```

### Tracing & metrics (forward-compatible, not core)

The **hop boundary is the single instrumentation point**. An optional `shal-otel`
package emits one OpenTelemetry span per hop (same attributes as log extras, `txn`
as the correlation key); core carries zero otel dependency. A metrics surface
(counters/latency per hop) attaches at the same boundary later.

### What it looks like

```
INFO    shal.loader      topology loaded: 7 nodes, 2 connections, 0 refs  [file=setup.yaml schema=1]
DEBUG   shal.bus.ssh     connect                       [path=/lab_server txn=a3f2 duration_ms=412]
DEBUG   shal.bus.i2c_cli txn w1 r2                     [path=/lab_server/i2c0/temp0 addr=0x48 txn=a3f2]
WARNING shal.bus.ssh     reconnect after drop (1/1)    [path=/lab_server txn=b7c1]
WARNING shal.route       failover /rack_a/i2c0 → /rack_b/i2c1  [id=chamber_temp txn=c9d0]
WARNING shal.watchdog    safe_state engaged after 300 ms silence  [id=cleaner]
```

App recipe (documentation, not API): `logging.basicConfig(level=logging.INFO)` for
humans; attach `JSONFormatter` for machines. That's it — SHAL stays out of the way.

## Security (trust boundaries, locked)

- **No shell strings** — `CommandTransport` carries argv (see Remote section). The
  single highest-risk surface, closed structurally.
- **`yaml.safe_load` only** (decision 1). The topology file is the front door of a
  widely installed package; arbitrary-object construction is instant RCE.
- **Address grammar validation at load** (decision 2) — hostile values never reach
  command construction.
- **Driver trust model** (decision 3) — entry points, installed-on-purpose,
  fail-on-missing, never import-by-config-string. Loading a topology must never
  fetch or execute anything new.
- **Network buses are authenticated and encrypted by default.** ssh brings it for
  free; `tcp`/`http`/`mqtt` require TLS; plaintext is a loud per-node
  `insecure: true` opt-out (the hobbyist's one-line escape hatch), never a default.
- **Secrets never live in topology files.** `${ENV_VAR}` interpolation + a pluggable
  secrets backend; people *will* commit `setup.yaml`. Resolved values never appear
  in logs (Logging rule 7).
- **Physical safety is a security property:** the actuator watchdog contract
  (Capabilities section) bounds what a lost or hijacked connection can leave moving.

## Lifecycle

- **Lazy open** (activation section); nothing connects until first use.
- **`with shal.load(...)` is the documented form.** Teardown order: cancel
  subscriptions → close connections **leaf→root** → release locks. Deterministic on
  exit and on exceptions.
- Bare `load()` works for REPL/notebooks; cleanup is best-effort (finalizers +
  `atexit`), explicitly documented as such.
- Reconnect semantics are owned by the Retry section; teardown never auto-reconnects.

## Validation (design-phase simulation harness)

*The standalone prototype that produced these results has been removed now that
the real implementation supersedes it; the Phase 1 test suite (`tests/`) and the
playground demos (`playground/`) are the live validation. The original findings:*

Ran the recursive model against 13 buses across embedded, automotive, industrial,
network, IoT, and lab worlds, over 5 topology files. All core mechanisms held:
recursion, addressing, id-vs-path lookup, remote CLI tunneling (I2C rendered to argv
carried by ssh, decoded back), mux select + `is_active` caching (2 selects for 3
reads), ssh connection caching (1 connect for 2 reads), dual-role nodes, stateful
local buses.

- **Sync (9):** i2c, i2c-mux, spi, pcie, usb, modbus, ssh, tcp, http — `txn`/`run`/
  `exchange`, lazy, stateless.
- **Async (4):** uart, can, ble, mqtt — held channels; remote ssh held `tail -f`
  streams with no agent; weakest-hop rule enforced (async behind non-streaming i2c
  fails loudly at setup while sync works on the same path).

**Next sim targets (from this revision):** delivered-but-unacked write under the
retry policy; subscription-pinned mux + sibling `shal.Busy`; two muxes sharing one
upstream bus (the v1 cache bug as a regression test).

### Phase 1 test suite (2026-06-10)

The Phase 1 implementation in `shal/` passes its pytest suite: **29 tests — 28
passed, 1 skipped** (`tests/test_buses.py:162`, an executable shim that needs
POSIX; skipped on Windows). Run: `python -m pytest tests/ -q` after
`pip install -e ".[dev]"`. No fixes were required on first execution.

### Sim transport and conformance kit are product, not scaffolding

- **`shal.sim` is a first-class shipped mock transport.** Running robot/control code
  against simulated buses with zero hardware is the adoption hook *and* a safety
  feature (test before you touch the real motor). This is the pytest move.
- **A conformance kit** lets a new bus / driver / capability self-certify against the
  contracts (kinds, retry marking, watchdog, logging fields). That's how the
  community extends without fragmenting.

## Open questions

**Resolved in `DECISIONS - V2.1.md`** (with Phase 1 implementation in `shal/`):
`get_device` positional shorthand (leading `/` = path, else id); JSON Schema for
`shal_version: 1` (`schema/shal-v1.schema.json`); error taxonomy; driver base API +
entry-point group `shal.drivers`; watchdog mechanics (designed, Phase 2 impl).

**Still open:**

- `agent` bus wire protocol.
- Hotplug / discovery — device tree is static; a *dynamic* HAL implies USB devices
  appearing and vanishing. Node lifecycle events, re-validation, id stability.
- Metrics API surface (beyond logs/traces).
- Capability RFC template + graduation mechanics (process is decided; paperwork isn't).
- asyncio internals for `stream()` (loop ownership, backpressure).

## Revision notes (v2)

Adopted from review: **argv-only CommandTransport** (kills shell injection
structurally); **typed payloads per transport kind** (resolves the decision-4
keystone; `Any` payload dropped); **retry/idempotency policy** (writes never silently
re-fired); **actuator safe-state/watchdog contract**; **per-mux selection cache**
(fixes cross-mux cache poisoning); **explicit mux delegation** (removes side-effecting
`__getattr__`); **stream-through-mux pinning + `shal.Busy`**; **concurrency model**;
**security section**; **lifecycle/context manager**; **sync-first streaming API with
asyncio flavor + `Gap` surfacing**; **capability semantics + semver**; **per-route
addresses in failover**; **`shal_version` + JSON Schema**; **logging & observability
section**; **sim transport + conformance kit promoted to product**. Drift fixed:
unified `driver:` key, `capability:` removed from YAML, `host`/`upstream` relation
settled, `$ref` targets ids, no-agent phrasing repealed in activation, decision 4
rewritten to match the kinds model.
