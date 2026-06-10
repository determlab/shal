"""Transport base + typed per-kind mixins (DESIGN V2 decision 4).

Only ``Transport`` has state/__init__; kind mixins are stateless -> no diamond.
Each kind owns its payload shape. Contents stay opaque within a kind.
"""
from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .node import Node


# ---- payload types ---------------------------------------------------------

@dataclass(frozen=True)
class Write:
    data: bytes


@dataclass(frozen=True)
class Read:
    n: int


Op = Write | Read  # sequences cover repeated-start write-then-read


@dataclass(frozen=True)
class Completed:
    stdout: bytes
    stderr: bytes
    exit: int


# ---- base ------------------------------------------------------------------

class Transport:
    """The only class in the hierarchy with state. One RLock per bus instance."""

    def __init__(self, host: Node) -> None:
        self.host = host
        self.lock = threading.RLock()
        self._active = False

    @property
    def upstream(self) -> Transport | None:
        """The bus exposed by the parent node — the only way up."""
        return self.host.parent_bus

    def kinds(self) -> frozenset[type]:
        """Honest introspection — validation uses this, never hasattr."""
        return frozenset(
            k for k in (ByteTransport, CommandTransport, MessageTransport, Stream)
            if isinstance(self, k)
        )

    # lifecycle ---------------------------------------------------------
    def is_active(self) -> bool:
        """Cheap LOCAL check — never a round-trip. Optimistic by contract;
        the retry/idempotency policy is the safety net."""
        return self._active

    def activate(self) -> None:
        self._active = True

    def ensure_ready(self) -> None:
        with self.lock:
            if not self.is_active():
                log = getattr(self, "log", None)
                if log is not None:  # buses bind one; activation = cache miss
                    log.debug("activate", event="activate")
                self.activate()

    def close(self) -> None:
        self._active = False


# ---- stateless kind mixins -------------------------------------------------

class ByteTransport:
    """Addressed byte transactions (i2c, spi, modbus, ...)."""

    def txn(self, addr: Any, ops: Sequence[Op]) -> bytes:
        raise NotImplementedError

    def validate_address(self, addr: Any) -> None:
        """Address grammar, enforced at load (decision 2). Override per family."""


class CommandTransport:
    """argv execution (ssh, local, container). No shell. Ever."""

    def run(self, argv: Sequence[str], stdin: bytes = b"") -> Completed:
        raise NotImplementedError

    def validate_address(self, addr: Any) -> None:
        pass


class MessageTransport:
    """Structured messages (http, mqtt, agent, rpc...)."""

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        raise NotImplementedError

    def validate_address(self, addr: Any) -> None:
        pass


class Stream:
    """Async push — held channel end-to-end (Phase 2)."""

    def subscribe(self, addr: Any, topic: str):
        raise NotImplementedError
