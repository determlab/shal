# Roadmap idea: agentic universal test station

Author: Nahum Paska
Date: 2026-11-25
Status: idea, not implemented

## Problem

Board bring-up and validation are still mostly manual. An engineer holds a probe,
reads an instrument, and decides what to measure next. AI agents can already plan
measurements and read results, but they have no safe, uniform way to operate a
whole bench: instruments, switching and physical access to the board.

## Idea

Use SHAL as the single control layer for a generic test station that an AI agent
can operate end to end, with approvals and an audit log for every action.

The station has four parts:

1. Modular instruments. A PXI chassis (or LAN bench instruments) with scope, DMM,
   power supplies / SMUs, signal generator, digital I/O and serial buses
   (I2C, SPI, UART, CAN, JTAG). Each card is exposed through a SHAL driver.
2. Switching. A relay matrix that can route any instrument to any low-speed pin.
   The matrix is described in the SHAL topology file, so the agent asks for
   "measure net X" and SHAL resolves the route.
3. Physical access.
   - A universal connector panel (pin headers, PCIe, USB, RJ45, M.2) wired into
     the matrix, with small interchangeable adapters per board type.
   - A robotic prober (gantry or flying-probe style) that lands a probe on any
     test point. Target coordinates come from the board's CAD data; a camera is
     used for alignment only. The prober is one more SHAL device.
4. High-speed path. Fast links (PCIe, USB3, fast Ethernet, memory) do not go
   through relays. They are exercised through a real host, loopback adapters and
   the device's built-in self-tests (pattern generators and checkers, lane
   margining, boundary scan) and through on-board telemetry (for example PMBus).
   SHAL orchestrates this path as well and merges the results.

## What SHAL adds

- One typed interface over instruments, switching, prober and host.
- Topology as data: nets, pins, routes and instrument capabilities in YAML with
  JSON Schema validation.
- Safety for agents: actions that drive or write (power on, force voltage, move
  the probe, flash) require approval; limits are enforced by SHAL, not by the
  model.
- Audit log: every action, route, reading and tool version is recorded, so a run
  can be replayed and traced.
- Vendor neutrality: the same agent workflow runs on NI, Keysight, Pickering or
  bench instruments by swapping drivers.

## First milestones

1. SHAL drivers for one PXI scope, one power supply / SMU and one switch matrix.
2. Topology schema extension for routes through a matrix ("net to instrument").
3. Prober device type: move to coordinate, touch, retract, with travel limits and
   an approval gate before each first touch on a new board.
4. Demo: an agent brings up one small board (rails, clocks, one serial bus) and
   produces a pass/fail report with the full audit trail.

## Open questions

- Probe accuracy needed for small pads and how to verify alignment before touch.
- Active probing on the prober head for faster signals.
- How to describe board-side access (top and bottom, tall components) in the
  topology file.
