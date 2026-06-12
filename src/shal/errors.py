"""SHAL exception hierarchy (DECISIONS v2.1 #3)."""
from __future__ import annotations

from typing import Literal

Delivered = Literal["no", "unknown"]


class Error(Exception):
    """Base for all SHAL errors."""


class LoadError(Error):
    """Anything wrong before runtime: schema, unknown compatible, duplicate id,
    bad address grammar, unresolved $ref, missing env var."""


class HopError(Error):
    """A hop in the recursion failed. Identity: (path, hop, txn)."""

    def __init__(
        self,
        msg: str,
        *,
        path: str = "?",
        hop: str = "?",
        txn: str = "----",
        delivered: Delivered = "no",
    ) -> None:
        super().__init__(f"{path}  {msg}   (hop: {hop}, txn={txn}, delivered={delivered})")
        self.path = path
        self.hop = hop
        self.txn = txn
        self.delivered: Delivered = delivered


class HopTimeout(HopError):
    def __init__(self, msg: str, *, which: Literal["hop", "budget"] = "hop", **kw) -> None:
        super().__init__(f"timeout ({which}): {msg}", **kw)
        self.which = which


class LimitError(Error):
    """An argument violated a declared operating limit (issue #10).

    Raised by the FRAMEWORK wrapper before any bus I/O — by construction the
    device never saw the command, so there is no `delivered` ambiguity.
    Deliberately NOT a HopError: nothing hopped. The message restates the limit
    so an LLM agent can self-correct; `violations` carries the structured form.
    """

    def __init__(self, msg: str, *, path: str = "?", op: str = "?",
                 violations: list | tuple = ()) -> None:
        super().__init__(msg)
        self.path = path
        self.op = op
        self.violations = list(violations)


class Busy(Error):
    """A mux channel is pinned by an active subscription (Phase 2)."""


class Gap:
    """Event marking a missed span in a subscription stream. NOT an exception."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"Gap({self.reason!r})"
