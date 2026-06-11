"""SHAL — System/Software Hardware Abstraction Layer.

    import shal
    with shal.load("setup.yaml") as hal:
        print(hal.get_device("ambient_temp").read_celsius())
"""
from . import logging  # opt-in observability: shal.logging.{Console,JSON}Formatter, capture
from .capabilities import TemperatureSensor
from .driver import Driver, idempotent, op
from .errors import Busy, Error, Gap, HopError, HopTimeout, LoadError
from .hal import Hal, load
from .node import Node
from .registry import catalog, register
from .transport import (
                        ByteTransport,
                        CommandTransport,
                        Completed,
                        MessageTransport,
                        Op,
                        Read,
                        Stream,
                        Transport,
                        Write,
)

__version__ = "0.1.0"

__all__ = [
    "load", "Hal", "Node", "Driver", "idempotent", "op", "register", "catalog", "logging",
    "Error", "LoadError", "HopError", "HopTimeout", "Busy", "Gap",
    "Transport", "ByteTransport", "CommandTransport", "MessageTransport",
    "Stream", "Op", "Read", "Write", "Completed",
    "TemperatureSensor", "__version__",
]
