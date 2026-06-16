# SHAL — agent guide (add a device, no MCP needed)

*This ships **inside** the `pyshal` package. Print it with `shal docs`. Provider-neutral:
any agent can follow it — no Claude-specific tooling, no reading SHAL's source.*

SHAL is a **device-agnostic framework**: it ships the machinery, not devices. To control
a device you (1) **wrap its Python library as a small driver**, (2) **describe your setup
in a topology YAML**, then (3) **read/serve it**. The safety gate is built in: reads run
free, writes that touch hardware stop for a human.

---

## The fastest path: wrap a Python library as a driver

Most devices already have a Python library (a speaker → `soco`, a vacuum → a cloud
client, an instrument → its SDK). A driver is one small class that calls that library.

```python
# my_driver.py  —  a "root" driver: it wraps a library directly, no SHAL bus
import shal
from shal import Driver, idempotent, op

@shal.register                       # registers it in-process (no packaging needed)
class MyThing(Driver, shal.MediaPlayer):   # a capability is OPTIONAL — see below
    compatible = "community,my-thing"      # lowercase "vendor,part" — the binding key
    kind = None                            # root driver: no parent bus, wraps a lib
    llm_ready = True                       # REQUIRED: enforces @op metadata at load

    def bind(self, node):
        super().bind(node)
        import the_library                 # import the device lib lazily, here
        self._client = the_library.Client(str(node.address))   # address from the YAML

    # --- a READ (free): side_effect="none". MUST be live or raise (never a default) ---
    @idempotent
    @op("Read the current volume (0-100).", side_effect="none")
    def get_volume(self) -> int:
        v = self._client.volume                # your library call
        if v is None:                          # no live answer? raise — don't guess
            raise shal.HopError("no response", path=self.node.path,
                                hop="lib", delivered="unknown")
        return int(v)

    # --- a WRITE: "write" = benign/instant (runs free); "actuator"/"config" = GATED ---
    @op("Set the volume (0-100).", side_effect="write")
    def set_volume(self, level: int) -> None:
        self._client.volume = int(level)
```

### The four rules
1. **`@shal.register`** + **`compatible`** (`"vendor,part"`) bind the class to the YAML.
2. **`llm_ready = True`**, and every public method has **`@op("description", side_effect=...)`**.
   Private helpers start with `_`.
3. **`side_effect`** is the gate:
   - `"none"` → a **read** (free). A read **must reflect a live device response or raise
     `shal.HopError`** — never return a cached/seeded/default value as if live.
   - `"write"` → a **benign** write (instant, reversible — runs free).
   - `"actuator"` (physical motion) / `"config"` → **gated**: stops for human approval.
   - *Forget to annotate? It defaults to gated (fail-closed).*
4. **`@idempotent`** on a read lets the framework auto-retry it; never on a write.

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
`config:` block — never literal in the file, never in logs.

---

## Run it (no MCP host needed)

```bash
shal probe my.yaml --drivers my_driver.py            # print device state and exit
shal probe my.yaml --drivers my_driver.py thing__get_volume   # one named read
shal tools my.yaml --drivers my_driver.py            # list the tools (read / gated)
shal mcp   my.yaml --drivers my_driver.py            # serve to an MCP host (the adapter)
```

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

**Going deeper:** the full SDK (buses, transport kinds, limits, sims, conformance) is at
<https://github.com/determlab/shal/blob/main/docs/SDK.md>.
