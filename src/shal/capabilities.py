"""Blessed core capability Protocols — the product.

A standard is its semantics, not its signatures: units in the name, semver,
specified ranges/errors. Phase 1 ships the first one.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class TemperatureSensor(Protocol):
    """v1.0.0 — read_celsius returns degrees Celsius as float.
    Range: sensor-specific; blocking: may block for one transaction;
    errors: raises shal.HopError on transport failure."""

    def read_celsius(self) -> float: ...


@runtime_checkable
class PowerMonitor(Protocol):
    """v1.0.0 — bus voltage (volts), current (amperes), power (watts), each as
    float. Sign convention: positive current = load draw. Errors: raises
    shal.HopError on transport failure."""

    def read_voltage(self) -> float: ...
    def read_current(self) -> float: ...
    def read_power(self) -> float: ...


@runtime_checkable
class PowerSupply(Protocol):
    """v1.0.0 — programmable DC supply. set_voltage is an absolute setpoint in
    volts; reads return volts/amperes; output(on) energizes the hardware."""

    def set_voltage(self, volts: float) -> None: ...
    def read_voltage(self) -> float: ...
    def read_current(self) -> float: ...
    def output(self, on: bool) -> None: ...


@runtime_checkable
class DigitalMultimeter(Protocol):
    """v1.0.0 — bench DMM; single-shot measurements as float, volts / amperes /
    ohms. Errors: raises shal.HopError on transport failure."""

    def measure_voltage_dc(self) -> float: ...
    def measure_current_dc(self) -> float: ...
    def measure_resistance(self) -> float: ...


@runtime_checkable
class ADC(Protocol):
    """v1.0.0 — analog-to-digital converter. read_voltage(channel) returns the
    input voltage in volts."""

    def read_voltage(self, channel: int = 0) -> float: ...


@runtime_checkable
class GPIOExpander(Protocol):
    """v1.0.0 — addressable digital I/O pins. Direction: output=True drives,
    False reads. Levels are booleans (high=True)."""

    def set_direction(self, pin: int, output: bool) -> None: ...
    def write_pin(self, pin: int, high: bool) -> None: ...
    def read_pin(self, pin: int) -> bool: ...
