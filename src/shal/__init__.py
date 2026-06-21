"""SHAL — System/Software Hardware Abstraction Layer.

    import shal
    with shal.load("setup.yaml") as hal:
        print(hal.get_device("ambient_temp").read_celsius())
"""
from importlib.metadata import PackageNotFoundError as _PkgNotFound
from importlib.metadata import version as _pkg_version

from . import logging  # opt-in observability: shal.logging.{Console,JSON}Formatter, capture
from .approval import (
    ApprovalRequest,
    Approver,
    AutoApprove,
    CallableApprover,
    ConsoleApprover,
    DenyAll,
    approver,
    get_approver,
    set_approver,
)
from .capabilities import (
    ADC,
    DigitalMultimeter,
    GPIOExpander,
    MediaPlayer,
    PowerMonitor,
    PowerSupply,
    TemperatureSensor,
)
from .driver import Driver, idempotent, op
from .errors import (
    ApprovalDenied,
    Busy,
    Error,
    Gap,
    HopError,
    HopTimeout,
    LimitError,
    LoadError,
)
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

# Single source of truth: the version declared in pyproject.toml. Deriving it from
# the installed package metadata keeps `shal.__version__` from drifting out of sync
# with the distribution (as it did when 0.2.0 shipped while this string still said
# 0.1.0). Falls back when running from an uninstalled source tree.
try:
    __version__ = _pkg_version("pyshal")
except _PkgNotFound:  # running from a source checkout without an install
    __version__ = "0.0.0.dev0"

__all__ = [
    "load", "Hal", "Node", "Driver", "idempotent", "op", "register", "catalog", "logging",
    "Approver", "ApprovalRequest", "AutoApprove", "DenyAll", "CallableApprover",
    "ConsoleApprover", "set_approver", "get_approver", "approver",
    "Error", "LoadError", "HopError", "HopTimeout", "LimitError", "ApprovalDenied",
    "Busy", "Gap",
    "Transport", "ByteTransport", "CommandTransport", "MessageTransport",
    "Stream", "Op", "Read", "Write", "Completed",
    "TemperatureSensor", "PowerMonitor", "PowerSupply", "DigitalMultimeter",
    "ADC", "GPIOExpander", "MediaPlayer", "__version__",
]
