# SHAL — System/Software Hardware Abstraction Layer

A framework for describing and controlling a HW and/or SW setup — a server wired to
eval boards over I2C, a home robot reached over WiFi, or any mix — from Python.
Inspired by the Linux device tree, but dynamic, user-space, and network-capable.

## Core idea

A **bus is just a node that provides a transport to its children.** Every link —
I2C, SPI, TCP/WiFi, SSH, USB, in-process — implements the same transport contract.
This makes the tree recursive: "SSH to a server that has an I2C controller with 4
boards" has the same shape as "WiFi to a robot with an internal SPI sensor."

Layering: **topology (declarative) → driver binding → capability API (what code calls).**

## Entities

- **Node** — anything in the tree (device, board, bus controller). Has an address
  within its parent, an optional `id`, and an auto-derived path.
- **Bus** — a node that exposes a transport API (`read` / `write` / `transaction` /
  `call`) to its children. Buses can nest arbitrarily.
- **Driver** — code bound to a node by a `compatible` id. Talks to the device using
  the *parent bus's* transport and exposes a typed capability.
- **Capability / interface** — the SAL part. `TemperatureSensor.read()`,
  `Motor.set_speed()`. Abstract, decoupled from how the device is reached.

## Key decisions (locked)

1. **Description format** — declarative topology in **YAML** + drivers/buses in
   **Python**. Chosen for lowest barrier to adoption (the priority for a "standard").

2. **Addressing** — path scheme `/server/i2c0/board2/temp0` that works across bus
   types. Each bus defines what an address means (I2C → `0x48`, TCP → host:port,
   SSH → user@host). Generalized `reg`.

3. **Driver binding** — registry keyed by a `compatible` string (`"ti,tmp102"`,
   `"myrobot,cleaner-v2"`). Node declares what it is; framework finds the driver.
   Community extends by publishing driver/bus packages, not editing core.

4. **Bus ↔ driver contract** — every bus implements a single polymorphic method:

   ```python
   class Bus:
       def exchange(self, addr, payload) -> result:
           ...
   ```

   Each bus subclass interprets `payload` its own way: I2C → bytes in/out,
   SSH → command/output, WiFi/HTTP → request/response. The driver passes what its
   device needs; driver and bus agree on the shape because a driver is written for a
   bus family. `payload`/`result` are untyped (`Any`) — trades static safety for one
   clean, easy-to-learn interface, the right call for an extensible standard.

5. **`id` field** — optional, **globally unique** (validated at load, fail loudly on
   dupes), location-independent handle. Path = location; id = stable semantic name.
   Moving a device between buses changes its path but not its id, so calling code
   doesn't break. Structural/bus nodes usually omit it.

## Lookup API

```python
hal = SHAL.load("setup.yaml")
dev = hal.get_device(id="ambient_temp")     # by stable id (common case)
dev = hal.get_device(path="/server/i2c0/temp0")  # by topology path
dev.read()                                   # capability call, transport-agnostic
```

## Example topology

```yaml
root:
  server:
    driver: shal,ssh-host
    address: user@10.0.0.5
    children:
      i2c0:
        driver: linux,i2c-dev
        address: /dev/i2c-1
        children:
          temp0:
            id: ambient_temp
            driver: ti,tmp102
            address: 0x48
            capability: TemperatureSensor
  robot:
    id: cleaner
    driver: myrobot,cleaner-v2
    address: 192.168.1.42
    capability: VacuumRobot
```

Same primitives describe both the eval-board server and the WiFi robot — only the
bus + driver differ.

## Propagation (recursive parent-bus model)

Each device holds the bus that connects it to its parent. A call unwinds **up** the
tree from the leaf to root; each hop asks its parent-bus to carry the payload.

```python
class Device:
    parent_bus
    def exchange(self, payload):
        return self.parent_bus.exchange(self.addr, payload)

class Bus:
    host                      # the device this bus lives on
    def exchange(self, addr, payload):
        inner = self.encode(addr, payload)   # wrap for this hop
        return self.host.exchange(inner)     # recurse up to host's parent
```

Base case = root, which executes locally (in-process) and stops the recursion.
A bus must know which device hosts it (`self.host`) — the only wiring requirement.

## Cycles: tree + references

Tree stays the readable backbone. A back-edge (e.g. PC that SSHes back to the server)
is a **reference** to an existing id, not a nested child — device-tree phandle style.

```yaml
back_link:
  bus: ssh
  to: $server        # reference, loader links instead of recursing
```

## Muxes

A mux is a node that is **also a bus** (same dual role as any forwarding node, but
local). Its `exchange` selects the channel on the upstream bus, then forwards.

```python
class MuxBus(Bus):
    def exchange(self, addr, payload):
        self.parent_bus.exchange(self.mux_addr, select(self.channel))
        return self.parent_bus.exchange(addr, payload)
```

Nested muxes just nest; address collisions vanish (different path = different node).
Wrinkle: all channels share one upstream bus → serial, re-select each call.

## Remote / multi-hop: the crossing is just another bus

A remote hop is **not a special mechanism baked into core** — it's a bus type the user
picks per link. Core stays agnostic. Interchangeable remote buses:

- `ssh` — render the lower op to a shell command, run it remotely (default; great for
  bringup, labs, eval boards, hobby robots). No on-device software needed.
- `agent` — a tiny SHAL on the far side receives ops natively (for high throughput;
  avoids per-call shell cost).
- `rpc` / `container` — later, same interface.

All implement `RequestResponse` (and `Stream` if they can hold a channel). Swapping
`bus: ssh` → `bus: agent` changes nothing else. "No-agent" is a **choice**, not a law.

The ssh strategy below is the default; the rest of the doc says "remote bus
(ssh/agent/...)" wherever it once said "no agent."

### Default: ssh renders-to-CLI

When the ssh bus crosses to a remote machine, the lower bus renders its operation as a
**shell command** and the string-carrying ssh bus runs it remotely. Far-side I2C
becomes "I2C-over-CLI."

```yaml
root:
  remote_server:
    bus: ssh
    address: user@10.0.0.5
    children:
      i2c0:
        bus: i2c-cli           # I2C rendered as shell commands, not local ioctl
        address: /dev/i2c-1
        children:
          temp:
            id: ambient
            driver: ti,tmp102
            address: 0x48
```

```python
class I2cCliBus(Bus):
    def exchange(self, addr, payload):
        cmd = f"i2ctransfer -y 1 w{len(payload)}@{addr} {to_args(payload)} r1"
        out = self.parent_bus.exchange(None, cmd)   # ssh runs it remotely
        return parse(out)
```

The ssh bus carries strings; the I2C bus renders bytes→command and parses the reply.
No SHAL on the far side.

Costs of the ssh-CLI default: each transaction = one ssh + CLI round-trip (slow,
serial); remote must have the CLI tool (e.g. i2c-tools); multi-byte/stateful sequences
are fiddlier to encode. When this cost matters, swap to the `agent` bus — same
interface, no other changes. (A persistent ssh session also helps; the real killer is
per-call connection, not the absence of an agent.)

## Path activation (opening muxes + connections)

A path is "open" when every hop along it is ready: connections (ssh/wifi) open, muxes
selected to the right channel. This is handled implicitly by the recursion, not a
separate root→leaf pass.

**How ordering works:** opening is **lazy / on-demand**. Everything funnels through
`parent.exchange`, and you physically cannot send a byte to a child without first
traversing its parent — so parents open on the way. A leaf-side readiness action (mux
select) is itself a message that must pass up through ssh, forcing ssh open first.
Result: hops open root→leaf even though the code unwinds leaf→root.

**Required invariant (write into the bus contract):** every readiness action must be
expressed as a message routed through the parent (`parent_bus.exchange(...)`). A bus
must NOT ready itself by touching far-side hardware directly — in a multi-hop remote
setup that hardware isn't local (the no-agent constraint), so it can only express
readiness as something its parent carries.

**Efficiency — `is_active()` so we don't re-activate every call:**

```python
class Bus:
    def ensure_ready(self):
        if not self.is_active():        # cheap, LOCAL check — never a round-trip
            self.activate()
```

- **Connection hops:** `is_active()` = socket alive & not dropped → skip reconnect.
- **Selection hops (mux):** parent bus caches `current_channel`, updated on each select.

```python
class MuxBus(Bus):
    def is_active(self):
        return self.parent_bus.current_channel == self.channel
    def activate(self):
        self.parent_bus.exchange(self.mux_addr, select(self.channel))
        self.parent_bus.current_channel = self.channel
```

So repeat access to the same channel pays nothing; only switching channels re-selects.

**Caveat — cache validity:** `is_active()` trusts cached state, valid only if SHAL is
the sole owner of the bus. If something outside SHAL touches the mux, the cache lies.
- Default: **trust cache** (fast, common case).
- Per-bus `verify=True`: `is_active()` reads back real state or always re-asserts
  (slower, for shared/untrusted buses).

**Concurrency:** keep a **lock around check→activate→talk** so a concurrent sibling
can't flip a shared mux's channel between your `is_active()` and your transaction.

## Transport kinds (refines decision 4)

Instead of one untyped `exchange` + a `supports_stream` flag, a bus declares which
**transport kinds** it implements, as mixins on a shared `Transport` base:

```python
class Transport:                 # real base: state + lifecycle (ensure_ready, host)
    def __init__(self, host): self.host = host

class RequestResponse:           # mixin, methods only, no state
    def exchange(self, addr, payload): ...

class Stream:                    # mixin, methods only, no state
    def subscribe(self, addr, callback): ...

class MqttBus(Transport, RequestResponse, Stream): ...   # implements both
```

- **Bounded set of kinds** (start with these 2; add Session/bidirectional only if a
  real bus forces it). This caps the "exchange(anything)->anything" risk.
- **Payload stays opaque within a kind** — a driver is written for its bus family, so
  bytes-vs-dict is fine; we type the *shape*, not the *contents*.
- **Mixins have no `__init__`/state** → only `Transport` initializes → no diamond.
- **Binding validation rides on path activation (leaf→root):** each hop asserts it
  supports the needed kind; first hop that doesn't → fail loudly at setup.
- **Forwarding buses (mux/passthrough) are transparent, not per-kind gates.** A mux,
  once its channel is selected, is electrically transparent: the child is on the parent
  bus. So model it as a selector that delegates *every* call to the parent after
  selecting — it inherits all the parent's transport kinds for free (add a new kind
  later, mux carries it unchanged). The only thing it adds is "select my channel first."

  ```python
  class MuxLink:
      def __getattr__(self, kind_method):        # exchange, subscribe, anything
          self.select(self.channel)
          return getattr(self.upbus, kind_method) # defer to parent bus
  ```

## Async / streaming (second primitive)

`exchange` is synchronous request/response (leaf→root, returns). Async is the mirror:
data originates at a leaf **unsolicited** and travels **root-ward** to user code.

Second primitive — **explicitly stateful, opt-in:**

```python
sub = dev.subscribe(callback)   # opens + HOLDS a persistent channel end-to-end
sub.cancel()                    # tears down every hop
```

**Constraint (the design):** async requires the channel to be **held open** for the
whole subscription, along the entire path. No lazy/stateless async. This dissolves the
no-agent tension — over ssh you run a **long-running streaming command** (`tail -f`,
a persistent reader) and keep reading stdout; still no SHAL on the far side, just a
held-open shell.

Two rules it forces:
1. **Every hop must support a held stream.** Weakest hop decides; if any bus can't
   hold one, the device can't do async. `subscribe` fails loudly at setup, not later.
2. **Sync stays lazy & stateless; async is stateful & explicit.** A bus declares
   `supports_stream`. Events propagate up via each bus forwarding to its parent's
   subscribers (mirror of `exchange`).

The lifecycle deliberately avoided for sync returns **only** for async — fair, because
a stream genuinely is stateful.

## Capabilities (first-class contracts)

Capabilities are **shared Protocols**, not driver-invented APIs — this is what gives
interop and is arguably the product. Code depends on the capability, never the driver.

```python
class TemperatureSensor(Protocol):          # the contract (the standard)
    def read_celsius(self) -> float: ...

class Tmp102(Driver, TemperatureSensor):    # driver implements the contract
    def read_celsius(self): ...
```

Any temp sensor is now interchangeable. Decisions:

- **Ownership:** a small *blessed core set* lives in SHAL (`TemperatureSensor`, `Motor`,
  `Camera`, ...), like Linux subsystems. Community proposes new ones that can graduate
  into core. Prevents the "every driver invents its own API" failure.
- **Inferred, not declared:** a device's capability comes from the *driver* (which
  Protocols it implements), NOT from a `capability:` field in the topology. Declaring it
  twice is redundant and can drift. (Drop `capability:` from earlier YAML examples.)

## Runtime robustness

### Errors — attribute to the failing hop
Failures unwind up through the recursion. Each hop wraps the error with its identity,
so you always know *where* it broke, not just that it broke.

```
ShalError: /lab/ri2c/rtemp  i2c NAK at 0x48   (hop: ssh -> i2c)
```

### Routing — one path by default
A node lives at exactly one spot in the tree → exactly one route (its single
`parent_bus`). A `$ref` is a name pointer, NOT a second parent — you never route
through it. So there is zero routing ambiguity by default.

### Failover — opt-in multi-route
Multiple real routes exist only if explicitly declared. Declared order = priority.

```yaml
rtemp:
  driver: ti,tmp102
  routes: [/lab/ri2c, /lab2/ri2c]   # primary first
```

- On hop error: retry via the next route; if all fail, raise ONE aggregated error
  listing each route's failure.
- **Sticky:** once connected on a route, stay there (no flapping); re-probe primary
  only on reconnect, not mid-stream.
- Async: commit to one route for the subscription's lifetime; on drop, failover =
  re-subscribe on the next.

### Timeouts — per-hop and overall
Two layered limits:
- **Per-hop:** each bus has its own timeout (ssh slow, i2c fast).
- **Overall:** a hard ceiling for the whole call.

Effective hop limit = **min(hop timeout, remaining budget)**; whichever fires first
wins. The budget shrinks as the call descends — a hop can't exceed what's left. The
error names which fired: `timeout: i2c hop (0.5s)` vs `timeout: overall budget (2s)`.

### Reconnect — auto for sync, surfaced for async
- **Sync `exchange`:** transparent. On a dead connection, re-open once and retry the
  call. The user never sees a transient blip.
- **Async `subscribe`:** the drop is **surfaced to the callback** (e.g. an event/error)
  because data may have been missed during the gap — the user must know, not be lied to.
  Then failover/re-subscribe per the routing rules above.

## Validation (simulation harness — see sim/)

Ran the recursive model against 13 buses across embedded, automotive, industrial,
network, IoT, and lab worlds, over 5 topology files. All core mechanisms held:
recursion, addressing, id-vs-path lookup, remote no-agent CLI tunneling (I2C rendered
to a shell string carried by ssh, decoded back), mux channel-select + `is_active`
caching (2 selects for 3 reads), ssh connection caching (1 connect for 2 reads),
dual-role nodes, stateful local buses.

**HOLDS (9):** i2c, i2c-mux, spi, pcie, usb, modbus, ssh, tcp, http — all addressed,
synchronous request/response.

**Resolved with the two-primitive model (all 13 buses now covered):**

- `exchange(addr,payload)->result` — synchronous, lazy, stateless (9 buses).
- `subscribe(addr,callback)->sub` — async push, explicitly held-open channel (uart,
  can, ble, mqtt). Proven in sim: MQTT push + cancel; **remote ssh held `tail -f`
  streams with NO agent**; weakest-hop rule enforced (async behind non-streaming i2c
  **fails loudly at setup**, while sync still works on the same path).

**Cycles break naive traversal.** The loader handles `$ref` back-edges (links instead
of recursing, so load terminates), but any tree-walk that follows children recurses
forever over a cycle. **Rule:** every walk must carry a visited-set guard.

## Open questions (not yet decided)

- Exact method signatures of the bus transport contract (decision 4 keystone).
- Capability interface definition mechanism (ABCs? duck typing? registry?).
- Lifecycle: connect/disconnect, lazy vs eager binding, error/reconnect semantics.
- Whether `get_device(id)` positional shorthand is allowed.
- Schema/validation strategy for the YAML.
