---
name: shal-build-yaml
description: Author or modify a SHAL topology YAML file (shal_version 1). Use when the user wants to describe a hardware/software setup — devices, buses, muxes, remote hops — as a SHAL tree, or when a topology fails to load.
---

# Build a SHAL topology YAML (DRAFT)

## Mental model

One tree. **A bus is just a node that provides a transport to its children.**
Where your Python runs is the root; every hop toward a device (ssh, I2C
controller, cloud portal, mux channel) is a nested node.

```yaml
shal_version: 1            # required, literal 1
root:
  <node-name>:             # lowercase [a-z0-9][a-z0-9_-]*
    driver: vendor,name    # REQUIRED for every functional node
    address: <per-family>  # exactly one of: address | routes | to
    id: stable_handle      # optional, globally unique, survives moves
    config: {...}          # optional driver/bus parameters (see Secrets)
    children: {...}        # devices reached THROUGH this node
```

## Procedure

1. **Read the schema first**: `shal/schema/shal-v1.schema.json` is the source
   of truth for allowed keys. Semantic rules (id uniqueness, address grammar,
   driver installed) are enforced by the loader, not the schema.
2. **Walk the physical path** from the PC to each device; every hop becomes a
   nesting level. If you can't say what carries the bytes between two nodes,
   the tree is wrong.
3. **Pick drivers by `compatible`**. Bundled: `shal,local`, `shal,ssh-host`,
   `shal,i2c-cli`, `shal,spi-cli`, `shal,tcp`, `shal,http`, `shal,sim-i2c`,
   `nxp,pca9548`, `ti,tmp102`. A driver must be installed (entry point group
   `shal.drivers`) or explicitly registered — an unknown compatible fails the load.
4. **Address per bus family**: I2C `0x03`–`0x77` (int), i2c-cli `/dev/i2c-<n>`,
   spi-cli `/dev/spidevX.Y`, ssh `user@host`, tcp `host:port`, http(s) a URL.
   The PARENT validates the child's address at load.
5. **Give ids** to anything user code will look up: `hal.get_device("<id>")`.
   Path = location, id = identity; moving a device changes its path, never its id.
6. **Mux channels** are address-only nodes (no `driver:`) under the mux node.
7. **Validate by loading** — the loader IS the validator:
   ```python
   import shal; shal.load("setup.yaml")
   ```
   Every failure is a `LoadError` whose message names the node path and the fix.

## Secrets (non-negotiable)

Secrets never live in topology files. Use `${ENV_VAR}` references in `address`
or `config:` values — resolved from the environment at load; a missing variable
fails the load naming the VARIABLE, never a value. Literal values in `config:`
are permitted only for files that never leave the machine.

```yaml
config:
  user: ${ECOVACS_EMAIL}        # recommended
  password: ${ECOVACS_PASSWORD}
```

## Things that fail the load by design

- `routes:` (failover) — parses but rejects: Phase 2.
- `insecure: true` missing on a plaintext `http://` or tcp bus.
- Duplicate `id`, unknown `compatible`, malformed address, unresolved `$ref`,
  unknown node keys (schema is `additionalProperties: false`).

## Reusing a board: `use:` includes

To graft a reusable subtree (a board, a rack module) without copy-paste, put it
in its own file under a top-level `template:` node, then `use:` it:

```yaml
# boards/tmp102-board.yaml
shal_version: 1
template:
  driver: shal,sim-i2c
  address: "${busname}"            # quote ${...} inside flow maps (YAML quirk)
  children:
    temp0: { id: "${inst}_temp", driver: ti,tmp102, address: 0x48 }
```

```yaml
# setup.yaml
root:
  board_a:
    use: boards/tmp102-board.yaml
    with: { inst: a, busname: sim0 }   # ${param} substitution -> id a_temp
    address: sim9                      # use-site keys OVERRIDE the template
```

Rules: the template is the base, use-site keys override it; `with:` params resolve
before env `${VAR}`s; includes may chain and nest; cycles fail loudly; paths are
relative to the including file and may not escape the top-level file's directory.
Include the same board twice → namespace its ids via a `with:` param, or you'll
hit the duplicate-id error.

## Disambiguating drivers: `from:`

If two installed packages both provide the same `compatible`, the load fails
naming both. Pin the one you want with a node `from: <distribution-name>`.

## Back-links / cycles

A node that points BACK to an existing node (PC ssh'ing to the server) is a
`$ref`, never a nested copy: `to: $lab_server`. Refs are name pointers; they
are never routed through.
