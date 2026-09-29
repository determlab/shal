# Guide: from the simulator to a real rack

You finished the [README Quick Start](../README.md#quick-start) on the simulator. This
guide takes the same topology file to a real device, several devices, a remote hop and
an agent. Blocks marked `# runs-on-sim` on their first line need no hardware and no
network; a test runs them. The other blocks need the hardware or host named under
their heading.

## 1. Sim Quick Start

Three lines, recapped from the [README](../README.md#quick-start): install from the
repo, write a topology with a simulated bus and sensor, probe it.

```bash
# runs-on-sim
cat > sim.yaml <<'EOF'
shal_version: 1
root:
  bus:
    driver: shal,sim-i2c
    address: sim0
    children:
      temp0:
        id: ambient_temp
        driver: shal,sim-sensor
        address: 0x48
EOF
shal probe sim.yaml
```

Install first with `pip install git+https://github.com/determlab/shal`. The read
prints `ambient_temp__read_celsius: <number>` and exits 0.

## 2. First real device

Prerequisites:

- A Linux host with the chip wired to an I2C bus (SDA, SCL, power, common ground) and
  the bus enabled in the kernel (on a Raspberry Pi, `raspi-config`, Interface Options,
  I2C; the node is then `/dev/i2c-1`).
- `i2c-tools` on that host (`sudo apt install i2c-tools`). SHAL renders each I2C
  transaction as an `i2ctransfer` command line; that program is all it needs.
- Your user can open `/dev/i2c-*` (`sudo usermod -aG i2c $USER`, then log in again).
- Check the wiring with `i2cdetect -y 1`; the chip's address (for example `48`) must
  show up.
- A driver for your chip. `shal,sim-sensor` is a simulated part, not a real one. Start
  from a reference: `shal docs --list`, then `shal docs --example tmp102`, and
  see [CATALOG.md](CATALOG.md) for where a new driver goes and what to name it.

Swap the simulated bus for a real one. The real I2C bus is `shal,i2c-cli`. It
does not touch the hardware itself: it sits under a command transport, and here that is
`shal,local`, which runs commands on this machine. The bus address becomes the device
node, `/dev/i2c-<n>`.

```yaml
shal_version: 1
root:
  host:
    driver: shal,local
    address: localhost
    children:
      i2c1:
        driver: shal,i2c-cli
        address: /dev/i2c-1
        children:
          temp0:
            id: ambient_temp
            driver: ti,tmp102        # your driver, or one built from the reference
            address: 0x48
```

Run it with your driver file named, as in the reference example:
`shal probe real.yaml --drivers driver.py`. The tool names (`ambient_temp__...`) and
your Python do not change from the simulator.

## 3. Multi-device topology

Prerequisites: stage 2 works, plus one more thing to talk to: a second bus on the same
host, or a network service.

A topology is a tree. Add a sibling of the first bus for the second transport. The
runnable block below uses two simulated buses to show the shape; on hardware, the second
bus is another `shal,i2c-cli` (`/dev/i2c-2`), or a network transport such as `shal,tcp`
(`address: host:port`, TLS by default) or `shal,scpi-raw` for a bench instrument.

```yaml
# runs-on-sim
shal_version: 1
root:
  bus_a:
    driver: shal,sim-i2c
    address: sim0
    children:
      temp0:
        id: ambient_temp
        driver: shal,sim-sensor
        address: 0x48
  bus_b:
    driver: shal,sim-i2c
    address: sim1
    children:
      temp1:
        id: board_temp
        driver: shal,sim-sensor
        address: 0x49
```

Every device with an `id` becomes its own set of tools: `ambient_temp__read_celsius`
and `board_temp__read_celsius`. Names must be unique across the file.

## 4. Remote hop

Prerequisites:

- A jumpbox or bench controller you can reach with `ssh`, with key-based login
  (SHAL runs `ssh` with `BatchMode=yes`, so a password prompt fails instead of hanging).
  Test it: `ssh user@jumpbox true`.
- The `ssh` client on the machine that runs SHAL.
- On the far side, nothing of SHAL: only standard command-line tools, for I2C
  `i2c-tools`. No agent, no Python, no daemon.

Put `shal,ssh-host` where `shal,local` was. Everything below it is rendered as argv and
sent over the connection (reused between calls). The `address` is what you would type
after `ssh`.

```yaml
shal_version: 1
root:
  bench:
    driver: shal,ssh-host
    address: user@jumpbox.example.net
    children:
      i2c1:
        driver: shal,i2c-cli
        address: /dev/i2c-1
        children:
          temp0:
            id: ambient_temp
            driver: ti,tmp102
            address: 0x48
```

The `address` can also come from the environment as `${BENCH_SSH}`, so the file holds
no host name or secret. A dropped connection is reported with `delivered`
(`no`, `unknown`): SHAL does not re-send a write it cannot prove was not delivered.

## 5. Expose it to an agent

Prerequisites: any topology from the stages above. The blocks below run on the
simulator.

Agents get typed tools, not shell access. From Python, `hal.tool_schemas()` lists them
(name, description, JSON input schema) and `hal.call_tool()` runs one.

```python
# runs-on-sim
import shal

hal = shal.load({
    "shal_version": 1,
    "root": {"bus": {
        "driver": "shal,sim-i2c", "address": "sim0",
        "children": {"temp0": {"id": "ambient_temp",
                               "driver": "shal,sim-sensor", "address": 0x48}},
    }},
})
names = [t["name"] for t in hal.tool_schemas()]
assert "ambient_temp__read_celsius" in names
out = hal.call_tool("ambient_temp__read_celsius", {})
assert out["ok"] is True
print(out)
```

From a shell, `shal tools --json` prints the same tools as one JSON document, each with
its `kind` (`read`, `write` or `gated`) and its `side_effect`:

```bash
# runs-on-sim
cat > sim.yaml <<'EOF'
shal_version: 1
root:
  bus:
    driver: shal,sim-i2c
    address: sim0
    children:
      temp0:
        id: ambient_temp
        driver: shal,sim-sensor
        address: 0x48
EOF
shal tools sim.yaml --json
shal call sim.yaml ambient_temp read_celsius --json
```

Mind the side effect of every tool. Each op declares one: `none` (a read) runs at once;
`write` changes only the node's own data and runs; `config` and `actuator` are gated and
stop for a person. `shal call` has no `--approve` flag, so the agent that runs a command
cannot approve its own call: it exits 2 with `"rejected": "approval"` and nothing is
sent. A person approves through an MCP host (`shal mcp sim.yaml`), or your code runs the
op inside `with shal.approver(<your Approver>):`. The same gate applies to real
hardware, so what an agent does on the simulator is what it will be allowed to do on the
rack.
