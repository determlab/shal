# SHAL

### Agent-native infrastructure for labs and production systems

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

## One model for hardware *and* software

Plenty of frameworks unify software services. Plenty of hardware frameworks
unify devices. **SHAL's advantage is that it treats both as first-class nodes in
the same topology.** A temperature sensor on I²C, a power supply over SCPI, a
firmware flasher run over SSH, and a manufacturing database behind HTTP all live
in the same graph — same lookup model, same retry semantics, same audit trail,
same observability stack. Automation, test code, and AI agents no longer need to
understand transports, protocols, or network boundaries; **they operate on
capabilities.** The result is a single operational model for an entire lab,
validation rack, or production line — hardware and software as parts of one
system, instead of separate worlds glued together by custom scripts.

```yaml
# lab.yaml — hardware and software in ONE graph.
# [core] ships with SHAL; [pkg] = a device/service driver you install or write
# (one small class — see the build-driver / build-bus guides in .claude/skills/).
shal_version: 1
root:
  bench:                       # one SSH hop to the bench controller        [core]
    driver: shal,ssh-host
    address: ${BENCH_SSH}
    children:
      i2c0:                    # I²C rendered as argv over the SSH hop       [core]
        driver: shal,i2c-cli
        address: /dev/i2c-1
        children:
          ambient:
            id: ambient_temp   # HARDWARE — TemperatureSensor               [core]
            driver: ti,tmp102
            address: 0x48
      flash0:
        id: dut_flasher        # HARDWARE — firmware flash = a CLI over SSH  [pkg]
        driver: acme,dfu-util
        address: /dev/ttyUSB0

  instruments:                 # raw-socket SCPI bus (MessageTransport)      [pkg]
    driver: acme,scpi
    address: 10.0.0.50:5025
    children:
      supply:
        id: dut_power          # HARDWARE — PowerSupply                      [pkg]
        driver: keysight,e36312
        address: ch1

  services:                    # HTTPS to internal services                 [core]
    driver: shal,http
    address: https://mes.lab.internal
    children:
      results:
        id: results_db         # SOFTWARE — ResultsStore                     [pkg]
        driver: acme,mes-results
        address: api/v2/results
```

```python
import shal

with shal.load("lab.yaml") as hal:
    # The SAME lookup + capability model for every node — hardware or software.
    # No transport, protocol, or network detail leaks into this code.
    temp = hal.get_device("ambient_temp").read_celsius()       # I²C sensor
    hal.get_device("dut_power").set_voltage(3.3)               # SCPI power supply
    hal.get_device("dut_flasher").flash("firmware.bin")        # CLI tool over SSH
    hal.get_device("results_db").record(                       # HTTP service
        part="DUT-0042", ambient_c=temp, status="pass")
```

Every call above rides the **same** machinery: a delivery-unknown write to the
power supply or the database is treated exactly like one to a motor — never
silently re-fired. One `txn` id correlates the I²C transaction, the SCPI socket
exchange, and the HTTP POST in a single log stream. And the whole graph is an
**agent tool catalog** for free:

```python
with shal.load("lab.yaml") as hal:
    tools = hal.tool_schemas()       # Anthropic tool-use defs for every device op
    hal.call_tool("dut_power__set_voltage", {"volts": 3.3})
    # the LLM picks 'dut_power__set_voltage' by capability — it never sees SCPI,
    # and hal.tool_catalog() flags it `side_effect: write` so the harness can gate it
```

Swap the SSH hop for `shal,local`, or any real service for its `shal,sim-i2c`-style
mock, and **none of the capability calls change** — the same property that lets
you test an entire rack before touching real hardware.

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
ruff check src tests      # lint
```

Design documents: [docs/DESIGN V2.md](docs/DESIGN%20V2.md) (architecture, locked
decisions) and [docs/DECISIONS - V2.1.md](docs/DECISIONS%20-%20V2.1.md) (Phase 1
implementation decisions). Phase 1 ships the sync core; async/streaming,
watchdog, and route failover are Phase 2. Contributing:
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT — see [LICENSE](LICENSE).
