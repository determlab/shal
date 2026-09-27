"""SHAL exception hierarchy (DECISIONS v2.1 #3)."""
from __future__ import annotations

from typing import Literal

Delivered = Literal["no", "unknown"]

# How to approve a gated op, in one line. ONE source (#186): the no-approver denial
# below and `shal call --json`'s refusal (`how_to_approve`) both use this constant.
HOW_TO_APPROVE_LINE = ("In Python: shal.approver(...) — AutoApprove() for a sim, or an "
                       "approver that asks a person; under an agent host: shal mcp.")
# The console approver denied because no one can answer: no approver is set and
# stdin is not a TTY (#186). Carried by ApprovalDenied with reason="no-approver".
NO_APPROVER_MESSAGE = "no approver is set and stdin is not a terminal. " + HOW_TO_APPROVE_LINE


class Error(Exception):
    """Base for all SHAL errors."""


class LoadError(Error):
    """Anything wrong before runtime: schema, unknown compatible, duplicate id,
    bad address grammar, unresolved $ref, missing env var."""


class HopError(Error):
    """A hop in the recursion failed. Identity: (path, hop, txn).

    ``via`` names the route that failed when the node has ``routes:`` (#235),
    in the object and in the text (``via=<name>``); ``None`` on an unrouted node,
    whose text is unchanged."""

    def __init__(
        self,
        msg: str,
        *,
        path: str = "?",
        hop: str = "?",
        txn: str = "----",
        delivered: Delivered = "no",
        via: str | None = None,
    ) -> None:
        self.path = path
        self.hop = hop
        self.txn = txn
        self.delivered: Delivered = delivered
        self.via = via
        self._msg = msg
        super().__init__(self._text())

    def _text(self) -> str:
        via = "" if self.via is None else f", via={self.via}"
        return (f"{self.path}  {self._msg}   (hop: {self.hop}, txn={self.txn}, "
                f"delivered={self.delivered}{via})")

    def with_via(self, via: str) -> HopError:
        """Name the route this error came through; returns the same error (its
        type, traceback and ``delivered`` untouched), so a route set re-raises it."""
        self.via = via
        self.args = (self._text(),)
        return self


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


class ApprovalDenied(Error):
    """A side-effecting (actuator) op was denied by the active Approver (issue #14).

    Like LimitError, the gate is pre-I/O — by construction the device never saw
    the command, so there is no `delivered` ambiguity, and it is deliberately NOT
    a HopError (nothing hopped). Carries op/path/params so the refusal lands in
    the audit trail and an agent gets a structured reason it can act on.
    """

    # every refusal says how to approve, so the reader is never stuck (#156)
    _HOW_TO_APPROVE = ("to approve: run it under an approver in Python — "
                       "`with shal.approver(...)` — or through an MCP host via "
                       "`shal mcp`, which asks a human")

    def __init__(self, msg: str, *, path: str = "?", op: str = "?",
                 side_effect: str = "actuator", params: dict | None = None,
                 reason: str | None = None) -> None:
        # ONE how-to hint per message: the no-approver line replaces the general one
        # (#186). Idempotent: unpickling calls __init__ again with the full message
        # (and no kwargs; `reason` comes back from __dict__).
        if not msg.endswith((self._HOW_TO_APPROVE, NO_APPROVER_MESSAGE)):
            hint = NO_APPROVER_MESSAGE if reason == "no-approver" else self._HOW_TO_APPROVE
            msg = f"{msg}; {hint}"
        super().__init__(msg)
        self.path = path
        self.op = op
        self.side_effect = side_effect
        self.params = dict(params or {})
        # why it was denied, for --json consumers: "no-approver" when the default
        # console approver had no one to ask; None when the approver gave no reason
        self.reason = reason


class Busy(Error):
    """A mux channel is pinned by an active subscription (Phase 2)."""


class Gap:
    """Event marking a missed span in a subscription stream. NOT an exception."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason

    def __repr__(self) -> str:  # pragma: no cover
        return f"Gap({self.reason!r})"
