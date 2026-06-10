# SHAL

**System/Software Hardware Abstraction Layer** — describe a hardware/software
setup in YAML, control it from Python. Inspired by the Linux device tree, but
dynamic, user-space, and network-capable.

A server wired to eval boards over I2C, a robot reached over the cloud, a DUT
behind an SSH jumpbox and an I2C mux — all the same recursive shape: **a bus is
just a node that provides a transport to its children.**

```yaml
# setup.yaml
shal_version: 1
root:
  lab_server:
    driver: shal,ssh-host
    address: ${SHAL_LAB_SSH}        # secrets resolve from the environment
    children:
      i2c0:
        driver: shal,i2c-cli        # I2C rendered as argv; far side needs only i2c-tools
        address: /dev/i2c-1
        children:
          temp0:
            id: ambient_temp
            driver: ti,tmp102
            address: 0x48
```

```python
import shal

with shal.load("setup.yaml") as hal:
    print(hal.get_device("ambient_temp").read_celsius())
```

## Install

```
pip install shal            # library
pip install -e ".[dev]"     # development (pytest, ruff)
```

Requires Python ≥ 3.10. Dependencies: `pyyaml`, `jsonschema`.

## What you get

- **Declarative topology** — versioned YAML (`shal_version: 1`) with a published
  JSON Schema; ids, paths, `$ref` back-links, per-node `config:` with `${ENV}`
  secret resolution.
- **Typed transport kinds** — `ByteTransport` (I2C/SPI), `CommandTransport`
  (argv — never shell strings), `MessageTransport` (JSON), with honest `kinds()`
  introspection validated at load.
- **Bundled buses** — `shal,local`, `shal,ssh-host` (ControlMaster reuse),
  `shal,i2c-cli`, `shal,spi-cli`, `shal,tcp` (TLS by default), `shal,http`,
  `nxp,pca9548` mux (per-mux selection cache), and `shal,sim-i2c` — a
  first-class simulated bus: test before you touch the real motor.
- **Capabilities, not driver APIs** — code depends on Protocols
  (`TemperatureSensor.read_celsius()`), never on how the device is reached.
- **A retry policy you can trust** — idempotent ops reconnect once/retry once;
  a write with unknown delivery is **never** silently re-fired. You decide.
- **Drivers as plugins** — registry keyed by `compatible` (`"ti,tmp102"`),
  discovered via the `shal.drivers` entry-point group. The framework never
  imports a module named by a config string.

## Observability

SHAL emits structured records (stdlib `logging`, `shal.*` namespaces) and never
configures logging. Every record carries a stable `event` key plus
`path`/`hop`/`addr`/`txn`/`duration_ms` fields; one `txn` id correlates every
hop of one capability call.

```python
import logging, shal

logging.basicConfig(level=logging.INFO)
logging.getLogger().handlers[0].setFormatter(shal.logging.ConsoleFormatter())
# INFO    shal.bus.tcp   connect 10.0.0.5:9000 tls=True  [path=/net event=connect duration_ms=12.3]

with shal.logging.capture("debug.jsonl"):   # flight recorder: DEBUG, JSON-lines,
    dev.set_speed(2.0)                       # escaping exceptions included
```

`debug.jsonl` is designed to be handed to a human *or an AI assistant* whole:
machine-stable events, full causal chain, secrets redacted by construction.
Auditing of actuator commands is one line away:
`logging.getLogger("shal.audit").addHandler(...)`.

## Security posture

`yaml.safe_load` only · argv vectors, never shell strings · address grammars
validated at load · TLS by default with loud `insecure: true` opt-out · secrets
via `${ENV_VAR}` references, never logged, never in topology files.

## Development

```
python -m pytest          # test suite
ruff check shal tests     # lint
```

Design documents: [docs/DESIGN V2.md](docs/DESIGN%20V2.md) (architecture, locked
decisions) and [docs/DECISIONS - V2.1.md](docs/DECISIONS%20-%20V2.1.md) (Phase 1
implementation decisions). Phase 1 ships the sync core; async/streaming,
watchdog, and route failover are Phase 2. Contributing:
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
