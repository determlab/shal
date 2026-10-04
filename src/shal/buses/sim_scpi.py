"""shal,sim-scpi — simulated SCPI instrument rack (issue #10).

The MessageTransport twin of ``shal,sim-i2c``: device models are built from the
children's ``compatible`` at activation and answer the SAME ``{"scpi": cmd,
"query": bool} -> {"reply": text}`` contract as ``shal,scpi-raw`` — so a SCPI
driver runs UNCHANGED against the sim or the real instrument (test before you
touch the real supply).

A model is a class registered with ``@scpi_sim_model("vendor,part")`` exposing
``scpi(cmd: str) -> str`` (return "" for writes). One model instance per child
node, keyed by the child's address.

It also ships the sim family's second device, ``shal,sim-psu`` (ops#117 CTO
ruling 1, prerequisite C7, #252): a programmable bench supply with a
configurable resistive load, so the v1 story (``psu.set_voltage``) has a device
to run on with no hardware. Like ``shal,sim-sensor``, it wraps no part, so it
binds only under a ``shal,sim-scpi`` bus — the framework's own object.

And the third, ``shal,sim-dmm`` (#303): a bench DMM whose ``config.probe``
names a ``shal,sim-psu`` address on this same bus — its reading is that PSU's
own ``measure_voltage``/``measure_current``, plus small seeded noise. Reads
only (``side_effect="none"``); like the other two, it wraps no part.
"""
from __future__ import annotations

import logging
import os
import random
import re
from collections.abc import Mapping
from typing import Any

from ..driver import Driver, idempotent
from ..driver import op as _op  # `op` is the loop name in the model below
from ..errors import HopError, LoadError
from ..log import bus_logger, current_txn, redact_url
from ..node import Node
from ..transport import MessageTransport, Transport

logger = logging.getLogger("shal.bus.sim_scpi")

SCPI_SIM_MODELS: dict[str, type] = {}


def scpi_sim_model(compatible: str):
    """Register a SCPI device model for ``shal,sim-scpi`` (mirrors sim-i2c's
    ``@sim_model``). The model class needs one method: ``scpi(cmd) -> str``."""
    def deco(cls):
        SCPI_SIM_MODELS[compatible] = cls
        return cls
    return deco


class SimScpiBus(Driver, Transport, MessageTransport):
    """A node that provides MessageTransport (scpi-raw dialect) to its children —
    entirely in memory."""

    compatible = "shal,sim-scpi"
    kind = None  # may sit at root, or behind any CommandTransport later

    def __init__(self, node: Node) -> None:
        Transport.__init__(self, node)
        self._models: dict[Any, Any] = {}
        self._unmodeled: dict[Any, str] = {}   # declared children with no sim model
        self._devices: dict[Any, Node] = {}    # every declared child, keyed by address
        self._unplugged: set[Any] = set()      # addresses faulted (#304): fault:
        # unplugged or SHAL_SIM_UNPLUG=<id>, a refused connection, not a NAK
        self.fail_next: int = 0          # test hook: fail N next txns (delivered=no)
        self.fail_delivered_unknown = False  # test hook: ambiguous failure
        self.connect_count = 0
        self.log = bus_logger("sim_scpi", node.path)

    def validate_address(self, addr: Any) -> None:
        if not isinstance(addr, (str, int)) or str(addr) == "":
            # redact_url: child address is ${ENV}-resolved, so its content
            # isn't constrained by the expected label grammar (#126)
            raise LoadError(f"sim-scpi: child address must be a non-empty "
                            f"instrument/channel label, got "
                            f"{redact_url(str(addr))!r}")

    def activate(self) -> None:
        self.connect_count += 1
        unplug_id = os.environ.get("SHAL_SIM_UNPLUG")
        for node in self.host.walk():
            if node is self.host:
                continue
            comp = getattr(node, "spec", {}).get("driver")
            model_cls = SCPI_SIM_MODELS.get(comp)
            if node.address is not None:
                self._devices.setdefault(node.address, node)
                if model_cls is not None:
                    if node.address not in self._models:
                        model = model_cls()
                        # a model that needs more than its own wire state (e.g.
                        # sim-dmm probing another address on this same bus) gets
                        # one, after every node is bound, via this optional hook
                        bind_sim = getattr(model, "bind_sim", None)
                        if bind_sim is not None:
                            bind_sim(self, node)
                        self._models[node.address] = model
                elif isinstance(comp, str):
                    self._unmodeled.setdefault(node.address, comp)
                if node.spec.get("fault") == "unplugged" or (
                        unplug_id is not None and node.id == unplug_id):
                    self._unplugged.add(node.address)
        super().activate()
        self.log.debug("connect (%d instrument models)", len(self._models),
                       event="connect")

    def model_for(self, addr: Any):
        self.ensure_ready()
        try:
            return self._models[addr]
        except KeyError:
            have = ", ".join(repr(redact_url(str(a))) for a in self._models) or "none"
            raise LookupError(f"sim-scpi: no sim model at {redact_url(str(addr))!r}; "
                              f"addresses with models: {have}") from None

    def device_for(self, addr: Any):
        """The bound Driver at ``addr`` on this bus — for a sim model that needs
        another device's own computed reading (e.g. sim-dmm probing sim-psu),
        not just its raw wire state (``model_for``)."""
        self.ensure_ready()
        try:
            return self._devices[addr].driver
        except KeyError:
            have = ", ".join(repr(redact_url(str(a))) for a in self._devices) or "none"
            raise LookupError(f"sim-scpi: no device at {redact_url(str(addr))!r}; "
                              f"addresses on this bus: {have}") from None

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        with self.lock:  # check -> activate -> talk, under the bus lock
            self.ensure_ready()
            if self.fail_delivered_unknown:
                self.fail_delivered_unknown = False
                self._active = False
                raise HopError("connection lost after send", path=self.host.path,
                               hop="sim-scpi", txn=current_txn.get(),
                               delivered="unknown")
            if self.fail_next > 0:
                self.fail_next -= 1
                self._active = False
                raise HopError("simulated link drop before send",
                               path=self.host.path, hop="sim-scpi",
                               txn=current_txn.get(), delivered="no")
            if addr in self._unplugged:
                # redact_url: address is ${ENV}-resolved (#126); delivered="no" —
                # refused exactly like a real disconnected link (#304)
                raise HopError(f"no answer from the instrument at "
                               f"{redact_url(str(addr))!r}", path=self.host.path,
                               hop="sim-scpi", txn=current_txn.get(), delivered="no")
            model = self._models.get(addr)
            if model is None:
                # redact_url: address is ${ENV}-resolved, so its content isn't
                # constrained by the expected label grammar (#126)
                hint = ""
                if addr in self._unmodeled:
                    comp = self._unmodeled[addr]
                    hint = (f" — no sim model registered for {comp!r}; "
                            f"decorate a class with @scpi_sim_model({comp!r})")
                raise HopError(f"no instrument at {redact_url(str(addr))!r}{hint}",
                               path=self.host.path,
                               hop="sim-scpi", txn=current_txn.get())
            reply = model.scpi(msg["scpi"])
            self.log.debug("%s %r", "query" if msg.get("query") else "write",
                           msg["scpi"], event="exchange", addr=str(addr))
            return {"reply": reply}


# -- the sim device: shal,sim-psu -----------------------------------------------------

@scpi_sim_model("shal,sim-psu")
class SimPsuModel:
    """Behavioural model of a programmable bench supply's output: a ``VOLT <v>``
    sets the output volts, and ``MEAS:VOLT?`` reads back exactly what was last
    set — a sim twin has no drift or accuracy error, so the live value IS this
    state (rule 3, same as ``SimSensorModel``). The load and the Ohm's-law
    current derived from it live on the driver (the node's own config), not
    here: the model only answers what a real instrument's firmware would."""

    _SET_V = re.compile(r"^VOLT\s+([0-9.eE+-]+)$")

    def __init__(self) -> None:
        self.voltage = 0.0
        self.set_count = 0   # test hook: set_voltage writes that reached the model

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()
        if m := self._SET_V.match(cmd):
            self.voltage = float(m.group(1))
            self.set_count += 1
            return ""
        if cmd == "MEAS:VOLT?":
            return f"{self.voltage:.6f}"
        return ""


class SimPsu(Driver):
    """Simulated bench PSU that ships with SHAL (ops#117 CTO ruling 1, #252):
    ``set_voltage`` energizes the (simulated) output now, so it is gated as
    ``actuator``; ``measure_current`` follows Ohm's law from the set voltage
    and this node's configured ``load_ohms``."""

    compatible = "shal,sim-psu"
    kind = MessageTransport
    llm_ready = True

    DEFAULT_LOAD_OHMS = 10.0

    def bind(self, node: Node) -> None:
        # it wraps no part: on a real bus it would talk to whatever instrument
        # answers at this address, so only a shal,sim-scpi ancestor may carry it
        parent = node.parent
        while parent is not None and not isinstance(parent.driver, SimScpiBus):
            parent = parent.parent
        if parent is None:
            raise LoadError(f"{node.path}: shal,sim-psu is a simulated device — "
                            f"put it under a shal,sim-scpi bus")
        super().bind(node)
        config = node.spec.get("config", {}) or {}
        load_ohms = config.get("load_ohms", self.DEFAULT_LOAD_OHMS)
        if isinstance(load_ohms, bool) or not isinstance(load_ohms, (int, float)) \
                or load_ohms <= 0:
            raise LoadError(f"{node.path}: config.load_ohms must be a positive "
                            f"number, got {load_ohms!r}")
        self.load_ohms = float(load_ohms)
        current_limit = config.get("current_limit")
        if current_limit is not None and (
                isinstance(current_limit, bool)
                or not isinstance(current_limit, (int, float)) or current_limit <= 0):
            raise LoadError(f"{node.path}: config.current_limit must be a positive "
                            f"number of amperes, got {current_limit!r}; set it above "
                            f"0 or remove it for an unlimited supply")
        self.current_limit = None if current_limit is None else float(current_limit)

    def _set_volts(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT?", "query": True})
        return float(reply["reply"])

    def _in_cc_mode(self, volts: float) -> bool:
        return (self.current_limit is not None
                and volts / self.load_ohms > self.current_limit)

    @idempotent  # an absolute setpoint: re-asserting the same volts is safe
    @_op("Set the PSU's output voltage (absolute setpoint). This energizes the "
        "(simulated) output now, so it needs approval.", unit="volt",
        side_effect="actuator", params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> None:
        self.bus.exchange(self.addr, {"scpi": f"VOLT {volts}"})

    @idempotent  # a read: safe to auto-retry across transient drops
    @_op("Read the measured output voltage now.", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        volts = self._set_volts()
        if self._in_cc_mode(volts):   # constant-current: the output sags to I*R
            return self.current_limit * self.load_ohms
        return volts

    @idempotent
    @_op("Read the measured output current now (Ohm's law: the set voltage "
        "divided by this node's configured load, capped at its current_limit).",
        unit="ampere", side_effect="none")
    def measure_current(self) -> float:
        volts = self._set_volts()
        if self._in_cc_mode(volts):
            return self.current_limit
        return volts / self.load_ohms

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "instrument/channel label on the "
                                              "sim SCPI bus",
                               "examples": ["psu0"]},
            "config_schema": {"type": "object", "properties": {
                "load_ohms": {"type": "number", "exclusiveMinimum": 0,
                              "description": "resistive load across the output, "
                                             "in ohms"},
                "current_limit": {"type": "number", "exclusiveMinimum": 0,
                                  "description": "optional current limit in amperes; "
                                                 "above it the output is constant-"
                                                 "current and the voltage sags"}},
                              "additionalProperties": False},
        }


# -- the sim device: shal,sim-dmm -----------------------------------------------------

@scpi_sim_model("shal,sim-dmm")
class SimDmmModel:
    """Behavioural model of a bench DMM (issue #303): ``MEAS:VOLT:DC?`` and
    ``MEAS:CURR:DC?`` read the ``shal,sim-psu`` named by this node's
    ``config.probe`` (the same bus) through ITS OWN ``measure_voltage``/
    ``measure_current`` — so load_ohms and constant-current mode already
    apply — plus small seeded noise (a DMM's own measurement error, not the
    PSU's). The noise is seeded from this node's path and the probed address,
    so the same topology gives the same reading on every fresh load; the live
    value IS this computation, nothing is cached across calls (rule 3)."""

    NOISE_FRAC = 0.003   # +/- ~0.3%, comfortably inside the 1% DoD tolerance

    def __init__(self, rng: random.Random | None = None) -> None:
        self._rng = rng
        self._bus: SimScpiBus | None = None
        self._probe_addr: Any = None

    def bind_sim(self, bus: SimScpiBus, node: Node) -> None:
        self._bus = bus
        self._probe_addr = (node.spec.get("config", {}) or {}).get("probe")
        if self._rng is None:
            self._rng = random.Random(f"{node.path}:{self._probe_addr}")

    def _noisy(self, value: float) -> float:
        return value * (1.0 + self._rng.uniform(-self.NOISE_FRAC, self.NOISE_FRAC))

    def scpi(self, cmd: str) -> str:
        cmd = cmd.strip()
        psu = self._bus.device_for(self._probe_addr)
        if cmd == "MEAS:VOLT:DC?":
            return f"{self._noisy(psu.measure_voltage()):.6f}"
        if cmd == "MEAS:CURR:DC?":
            return f"{self._noisy(psu.measure_current()):.6f}"
        return ""


class SimDmm(Driver):
    """Simulated bench DMM that ships with SHAL (issue #303): reads the
    ``shal,sim-psu`` named by ``config.probe`` on this same ``shal,sim-scpi``
    bus. Both ops are reads (``side_effect="none"``) — a DMM never changes
    what it is measuring."""

    compatible = "shal,sim-dmm"
    kind = MessageTransport
    llm_ready = True

    def bind(self, node: Node) -> None:
        # it wraps no part: on a real bus it would talk to whatever instrument
        # answers at this address, so only a shal,sim-scpi ancestor may carry it
        parent = node.parent
        while parent is not None and not isinstance(parent.driver, SimScpiBus):
            parent = parent.parent
        if parent is None:
            raise LoadError(f"{node.path}: shal,sim-dmm is a simulated device — "
                            f"put it under a shal,sim-scpi bus")
        super().bind(node)
        config = node.spec.get("config", {}) or {}
        probe = config.get("probe")
        if not isinstance(probe, str) or probe == "":
            raise LoadError(f"{node.path}: config.probe must name the shal,sim-psu "
                            f"address to read, on this same shal,sim-scpi bus, "
                            f"got {probe!r}")
        self.probe_addr = probe

    @idempotent  # a read: safe to auto-retry across transient drops
    @_op("Read the measured DC voltage now (the probed PSU's output, plus "
        "small measurement noise).", unit="volt", side_effect="none")
    def measure_voltage(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:VOLT:DC?", "query": True})
        return float(reply["reply"])

    @idempotent
    @_op("Read the measured DC current now (the probed PSU's output current "
        "through its configured load, plus small measurement noise).",
        unit="ampere", side_effect="none")
    def measure_current(self) -> float:
        reply = self.bus.exchange(self.addr, {"scpi": "MEAS:CURR:DC?", "query": True})
        return float(reply["reply"])

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "instrument/channel label on the "
                                              "sim SCPI bus",
                               "examples": ["dmm0"]},
            "config_schema": {"type": "object", "properties": {
                "probe": {"type": "string", "minLength": 1,
                          "description": "the shal,sim-psu address on this same "
                                         "bus whose output this DMM reads"}},
                              "required": ["probe"],
                              "additionalProperties": False},
        }


from .. import registry  # noqa: E402

registry.register(SimScpiBus)
registry.register(SimPsu)
registry.register(SimDmm)
