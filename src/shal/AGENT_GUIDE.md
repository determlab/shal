---
type: reference
owner: repo-agent
scope: repo/shal
reviewed: 2026-06-23
---

# SHAL — agent guide (add a device, no MCP needed)

*This ships **inside** the `pyshal` package. Print it with `shal docs`. Provider-neutral:
any agent can follow it — no Claude-specific tooling, no reading SHAL's source.*

> **Install `pyshal`, import `shal`.** The PyPI distribution is `pyshal`
> (`pip install pyshal[mcp]`); the Python module you import is `shal`
> (`import shal`). There is no module named `pyshal`.

SHAL is a **device-agnostic framework**: it ships the machinery, not devices. To control
a device you (1) **wrap its Python library as a small driver**, (2) **describe your setup
in a topology YAML**, then (3) **read/serve it**. The safety gate is built in: reads run
free, writes that touch hardware stop for a human.

---

## The fastest path: wrap a Python library as a driver

Most devices already have a Python library (a speaker → `soco`, a vacuum → a cloud
client, an instrument → its SDK). A driver is one small class that calls that library.

> **Mind the device library's own Python floor.** SHAL core runs on Python 3.10+,
> but a device's library may need newer. The maintained Deebot client
> (`deebot-client`) uses `asyncio.TaskGroup`, so it needs **Python 3.11+** — on 3.10
> its live commands fail. Make the venv on the version the *device library* requires
> (3.11+ for Deebot); no backport hacks.

> **Windows + MQTT drivers.** Every `shal` command (`probe`, `tools`, `mcp`) already
> sets the right event-loop policy. If
> you drive an `aiomqtt`-based device (e.g. Deebot) from your **own** script on Windows,
> the default `ProactorEventLoop` lacks `add_reader` (`NotImplementedError`); add one
> line before any asyncio call:
> `import asyncio, sys; sys.platform=="win32" and asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())`

```python
# my_driver.py  —  a "root" driver: it wraps a library directly, no SHAL bus
import shal
from shal import Driver, idempotent, op

@shal.register                       # registers it in-process (no packaging needed)
class MyThing(Driver):                     # a capability is OPTIONAL — see below
    compatible = "community,my-thing"      # lowercase "vendor,part" — the binding key
    kind = None                            # root driver: no parent bus, wraps a lib
    llm_ready = True                       # REQUIRED: enforces @op metadata at load

    def bind(self, node):
        super().bind(node)
        self._addr = str(node.address)     # bind() ONLY reads config — NO I/O here
        self._client = None                # connect lazily (see rule 5)

    def _ensure_connected(self):           # connect ONCE, on the first real op
        if self._client is None:
            import the_library             # import + connect here, not in bind()
            self._client = the_library.Client(self._addr)
        return self._client

    # --- a READ (free): side_effect="none". MUST be live or raise (never a default) ---
    @idempotent
    @op("Read the current volume (0-100).", side_effect="none")
    def get_volume(self) -> int:
        v = self._ensure_connected().volume    # your library call
        if v is None:                          # no live answer / not ready? raise — don't guess
            raise shal.HopError("no response", path=self.node.path,
                                hop="lib", delivered="unknown")
        return int(v)

    # --- a WRITE: "write" = benign/instant (runs free); "actuator"/"config" = GATED ---
    @op("Set the volume (0-100).", side_effect="write")
    def set_volume(self, level: int) -> None:
        self._ensure_connected().volume = int(level)
```

### The five rules
1. **`@shal.register`** + **`compatible`** (`"vendor,part"`) bind the class to the YAML.
2. **`llm_ready = True`**, and every public method has **`@op("description", side_effect=...)`**.
   Private helpers start with `_`.
3. **`side_effect`** is the gate:
   - `"none"` → a **read** (free). A read **must reflect a live device response or raise
     `shal.HopError`** — never a cached/seeded/default value, and never an
     **empty / "no data yet" success** (raise instead; the device just isn't ready).
   - `"write"` → a **benign** write (instant, reversible — runs free).
   - `"actuator"` (physical motion) / `"config"` → **gated**: stops for human approval.
   - *Forget to annotate? It defaults to gated (fail-closed), and `check_driver` warns.*
4. **`@idempotent`** on a read lets the framework auto-retry it; never on a write.
5. **Connect lazily — never in `bind()`.** `bind()` only reads config; open the connection
   on the first real op via a guarded `_ensure_connected()` (above). Why: `shal tools` /
   `shal mcp` list the tool surface **offline** (tool metadata is static), so if `bind()`
   authenticates or opens a session, merely *listing* the tools forces a live round-trip —
   and fails when the device/cloud is unreachable. A cheap local handle is fine in `bind()`;
   anything networked must be lazy.

### Wrapping an **async** library (run it on a dedicated loop thread)
If the library is `asyncio`-based, do **not** call `asyncio.run()` / `loop.run_until_complete()`
straight inside a sync `@op`. Under `shal mcp` your op runs while the server's event loop is
already active, so a fresh loop run raises *"Cannot run the event loop while another loop is
running"* — and a one-shot loop also lets the library's background tasks (e.g. MQTT
subscriptions) die between calls. Run the library on **one persistent loop on a dedicated
thread** and submit coroutines to it:

```python
import asyncio, threading
# in bind(): self._loop = None
def _run(self, coro):                       # call from any @op, sync or under shal mcp
    if self._loop is None:                  # start the loop thread once, lazily
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
    return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout=120)
```
The loop lives in its own thread, so the driver behaves identically under `shal probe` and
`shal mcp`, and long-lived subscriptions keep receiving while you poll for a live event.

### Capabilities are optional
Subclass a capability (`shal.MediaPlayer`, `shal.TemperatureSensor`, …) only when you want
your driver to be **interchangeable** with other drivers of the same kind. Otherwise just
expose `@op` methods — the agent sees them either way. (`shal.catalog()` lists what exists.)

---

## Describe the setup (topology YAML)

```yaml
shal_version: 1
root:
  my_device: {id: thing, driver: 'community,my-thing', address: '192.168.1.50'}
```
`shal.load()` also accepts this as an in-memory dict. Secrets go via `${ENV_VAR}` in a
`config:` block — never literal in the file, never in logs. For an HTTP API, the token
is a header on the `shal,http` bus node — `config: {headers: {Authorization: "Bearer
${GH_TOKEN}"}}` — never in the driver; the driver sends `{"method": "GET", "path":
"runs", "query": {...}}` and reads `reply["json"]` (SDK §3, the request envelope).

A cloud device (no IP to give) still needs an `address` — every node needs exactly one
of `address` | `routes` | `to`, even if `config:` holds the real connection details:
```yaml
shal_version: 1
root:
  vacuum: {id: vac, driver: 'community,my-thing', address: vac-serial-123,
           config: {account: '${CLOUD_ACCOUNT}'}}
```
Use a stable identifier — a serial number, account id, or the literal `"cloud"` — as
the address; `config:` alone does not satisfy the rule.

---

## Run it (no MCP host needed)

```bash
shal probe my.yaml --drivers my_driver.py                    # print device state and exit
shal probe my.yaml thing__get_volume --drivers my_driver.py # one named read (tool before --drivers)
shal tools my.yaml --drivers my_driver.py                   # list the tools (read / gated)
shal mcp   my.yaml --drivers my_driver.py                   # serve to an MCP host (the adapter)
```

> Tool names are `<node-id>__<op>` (e.g. `thing__get_volume`); several like devices
> disambiguate to `<id>_2__op`, `<id>_3__op`. Run `shal tools my.yaml` to see the exact
> handles — never hardcode them. A `--drivers <dir>` scan skips `_`-prefixed files; name a
> `_helper.py` explicitly on `--drivers` to load it.

Or in Python:
```python
import shal, my_driver          # importing my_driver runs @shal.register
with shal.load("my.yaml") as hal:
    print(hal.get_device("thing").get_volume())      # a read — free
```

---

## Verify before you trust it

```python
from shal import conformance
conformance.check_driver(MyThing)     # static + live sim checks; raises on a problem
```

The gate / approval flow (a write returns an `approval_required` ticket a human confirms)
is automatic — you don't wire it; you just classify ops with `side_effect`.

**Going deeper:** the full SDK (buses, transport kinds, limits, sims, conformance) is
bundled too — run `shal docs --sdk` (it ships in the wheel, like this guide).

---

## The ADK

You just used five things to build a driver. Together, they are the ADK — the
Agent Development Kit for SHAL. This section names them. It does not change how
they work.

1. **The rules in this guide.** See [The five rules](#the-five-rules) above.
   They tell you how to register a driver, how to tag every op with a
   `side_effect`, and why you must connect lazily.
2. **`@op` in [`driver.py`](driver.py).** `@op` is the contract behind rule 2 and
   3. It records the `side_effect` you declare, and if you forget it, the op is
   gated by default. It never becomes free by accident.
3. **The typed protocols in [`capabilities.py`](capabilities.py).** A capability
   like `PowerSupply` or `TemperatureSensor` names a set of methods. Claim one,
   and your driver works like any other driver of that kind.
4. **`conformance.check_driver` in [`conformance.py`](conformance.py).** This
   function checks your driver class. Give it a sim topology, and it also runs
   live checks against your driver. Its report says pass or fail.
5. **The sim-twin buses: [`buses/sim.py`](buses/sim.py),
   [`buses/sim_msg.py`](buses/sim_msg.py), and
   [`buses/sim_scpi.py`](buses/sim_scpi.py).** These run the same driver code
   against a simulated device, with no real hardware. This is what makes
   `check_driver`'s live checks possible without a lab.

### The order: declare, check, implement

Write the driver in this order, in one file:

1. **Declare.** Write the class with every `@op(description, side_effect, params=)`
   and `raise NotImplementedError` as each body.
2. **Check.** Run `check_driver(cls)` with no topology. The static checks pass, or
   they name the op that is missing a bound:
   ```python
   from shal.conformance import check_driver
   print(check_driver(MyThing))       # no topology: static checks only
   ```
   A real run, where `set_volume(self, level: int)` has no `params=`:
   ```text
   conformance community,my-thing: OK
     warning  set_volume: numeric write param 'level' has no declared limit — if the device has a safe operating range, declare it in @shal.op(params=...)
     checked  static: capability ops discovered
     checked  static: catalog entry + schemas well-formed
     checked  static: limit declarations reviewed
   ```
   A missing bound is a `warning`, so the report still says `OK`. Fix it anyway:
   `params={"level": {"minimum": 0, "maximum": 100}}` makes the warning go away.
   A `PROBLEM` line means the check failed.
3. **Implement.** Fill the bodies, then write the sim and the tests, and run the
   live checks (`check_driver(cls, topology)`).

The decorators are the contract. There is nothing else to write first.

### A failing check, and its fix

`shal check` runs the same checks from the shell. Run it in the folder that holds
`my_driver.py`:
```bash
shal check my_driver:MyThing --json
```
In this driver, `get_volume` has `@idempotent` but no `@op` line. A real run prints
this report and exits 1:
```json
{
  "compatible": "community,my-thing",
  "ok": false,
  "problems": [
    "get_volume: missing @shal.op description"
  ],
  "warnings": [],
  "checked": [
    "static: capability ops discovered",
    "static: catalog entry + schemas well-formed",
    "static: limit declarations reviewed"
  ]
}
```
`ok` is `false` because `problems` is not empty. An op problem starts with the name
of the op. The fix is one line, added directly above `def get_volume`:
```python
    @op("Read the current volume (0-100).", side_effect="none")
```
Run the same command again: it exits 0, and `problems` is `[]`. Exit 2 means the
check could not run (for example, a wrong `module:Class`); the reason is on stderr.

### Side effects for software

A database, a service or a CI system uses the same four labels. The label says
what the gate must stop:

- **`none`** — a read (`GET`, `SELECT`, a status). Live or raise.
- **`write`** — only if this driver can undo it with one of its own ops, and it
  touches only this node's own data. Example: insert a row that your `delete_row`
  op can remove. Runs free.
- **`config`** — changes what the system will do next. Example: set a repository
  variable. Gated.
- **`actuator`** — makes something happen now, outside the node, or cannot be
  undone. Example: trigger a CI run, or `DROP` a table. Gated.

**Not sure → gated.** An `UPDATE` that does not keep the old values cannot be
undone by this driver, so it is not a `write`.

**Bus or root driver?** A protocol you would hand-roll goes under a bus. A client
library is a root driver (`kind = None`, like the example above).

**What "proven" means.** These five pieces are the kit. They do not, by
themselves, show that a cold agent — one that has never seen a device before —
can use the kit to write a working driver on the first try. That claim needs a
test, not this page. The test lives in a separate repo, `determlab/adk-lab`: ten
devices, run cold, checked by a script, not by a person. Until `RESULTS.jsonl`
exists there from a real run, do not read this section as a passed test — it
names the kit; the lab is what proves it. See `adk.md`, decision R1.
