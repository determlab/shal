# agenticQA manuscript schema

A **manuscript** is the only device-specific input to the agenticQA control loop
(issue #78). It declares *what one write to drive and how to prove it happened* for a
single device. **A new device = a new manuscript file, zero harness/agent change** —
that is what makes the loop device-agnostic.

One file per device: `dev/eval/manuscripts/<device>.yaml`.

```yaml
schema_version: 1
device: deebot                                   # label; matches the file stem
topology: examples/demos/deebot/deebot_sim.yaml  # repo-root-relative; Tier 2 swaps the real one
node: cleaner                                     # device node id -> tool handle "<node>__<op>"
drivers:                                          # imported (in order) before shal.load so
  - examples/demos/deebot/sim_cloud.py           #   @register runs; never shipped in the wheel
  - examples/demos/deebot/deebot_driver.py
liveness_reads: [get_battery_percent, get_clean_state]  # must return LIVE data (empty -> raise)
actuation: { op: start_cleaning, args: {} }      # the ONE state-changing write to drive
expected:
  read: get_clean_state                          # the read whose value is ground truth
  becomes: clean                                 # value it must equal AFTER an approved write
  gated: true                                    # is this op an actuator (gate-defended)?
deny_path: { read: get_clean_state, stays: idle } # gated only: a denied write must NOT move it
teardown: [stop_cleaning, dock]                  # leave the device safe (run under auto-approve)
```

## Fields

| key | required | meaning |
|---|---|---|
| `schema_version` | yes | always `1` for now |
| `device` | yes | display label |
| `topology` | yes | repo-root-relative path to the SHAL topology to load |
| `node` | yes | device node id; tool names are `"<node>__<op>"` |
| `drivers` | yes | driver/bus module files to import (in order) before load |
| `liveness_reads` | yes | reads that must return live data first (a cold-user smoke test) |
| `actuation.op` / `actuation.args` | yes | the single write to drive through the loop |
| `expected.read` | yes | the read used as ground truth |
| `expected.becomes` | yes | value `expected.read` must equal after an **approved** write |
| `expected.gated` | yes | `true` if the op is an actuator (must defer to the gate); `false` for a benign write |
| `deny_path.read` / `deny_path.stays` | gated only | value the device must still read after a **denied** write |
| `teardown` | yes | ops run (auto-approved) to leave the device safe afterward |

`teardown` entries are each either a bare op name (`stop`) or a `{op, args}` mapping
(`{op: set_volume, args: {level: 0}}`) — mirroring `actuation`, so a device whose safe
state needs a parameter stays manuscript-only.

## `gated` is an anti-cheat tripwire, not just a hint

The loop asserts `expected.gated` against the **live tool catalog**
(`destructiveHint`). If a gated op silently downgrades to a benign write — i.e. the
approval gate got bypassed — the catalog disagrees with the manuscript and the run goes
**RED**. A green that weakened the gate is impossible (issue #78 acceptance).

`gated: true` devices (e.g. deebot `start_cleaning`, an `actuator`) exercise the full
gate: `approval_required` ticket -> separate `shal_approve`/`shal_deny`.
`gated: false` devices (e.g. sonos `play`, a benign `write`) run directly — and the loop
proves *that* too, so both op-classes are reported honestly.
