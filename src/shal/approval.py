"""Human-in-the-loop actuation gate (issue #14).

SHAL's promise — "it asks permission before it moves" — is enforced HERE, at the
same capability-wrapper chokepoint where limits and audit already fire. For an op
whose effective ``side_effect`` is ``"actuator"`` (physical motion), the wrapper
consults the active :class:`Approver` AFTER the limit check and BEFORE any bus
I/O. Because the gate lives in the wrapper, NO call path can bypass it — the tool
surface (``hal.call_tool``) and the raw path (``get_device().method()``) go
through the exact same enforcement.

SHAL ships the *mechanism* and a *safe default* (ask on the terminal when
interactive; deny when there is no one to ask — a pipe, a cron job, CI). The host
injects the *decision*:

    import shal
    shal.set_approver(shal.AutoApprove())          # sim / CI / tests
    with shal.approver(MyNanoClawApprover()):       # scoped policy
        ...

Order is always **limits -> approval -> I/O**: an impossible call is rejected by
limits before anyone is asked to approve it. Every decision (allow or deny) is
written to ``shal.audit`` so runs stay deterministic and replayable.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ApprovalRequest:
    """What the wrapper hands an Approver. Immutable, fully self-describing so a
    decision can be made (and logged) without reaching back into framework state."""
    op: str
    path: str
    id: str
    side_effect: str
    params: dict
    txn: str


@runtime_checkable
class Approver(Protocol):
    """A policy that decides whether a side-effecting op may proceed.

    Return ``True`` to allow, ``False`` to deny. Raising is treated by the caller
    as a hard failure (not a denial) — return ``False`` to refuse cleanly."""

    def approve(self, request: ApprovalRequest) -> bool: ...


class AutoApprove:
    """Allow every actuation. For sim, CI, and tests — never production."""

    def approve(self, request: ApprovalRequest) -> bool:
        return True


class DenyAll:
    """Refuse every actuation. The safest possible policy."""

    def approve(self, request: ApprovalRequest) -> bool:
        return False


class CallableApprover:
    """Adapt a plain ``fn(request) -> bool`` into an Approver (e.g. a NanoClaw
    callback, a queue handoff, a custom prompt)."""

    def __init__(self, fn: Callable[[ApprovalRequest], bool]) -> None:
        self._fn = fn

    def approve(self, request: ApprovalRequest) -> bool:
        return bool(self._fn(request))


class ConsoleApprover:
    """Ask on the terminal. The shipped default.

    If the input stream is not a TTY (a pipe, cron, CI), there is no one to
    answer, so it DENIES — never block a headless run on an unanswerable prompt.
    Inject :class:`AutoApprove` for those environments.
    """

    def __init__(self, *, stream=None, prompt: Callable[[str], str] = input) -> None:
        self._stream = stream
        self._prompt = prompt

    def approve(self, request: ApprovalRequest) -> bool:
        stream = self._stream or sys.stdin
        if not getattr(stream, "isatty", lambda: False)():
            return False  # no one to ask -> don't move
        args = ", ".join(f"{k}={v!r}" for k, v in request.params.items())
        who = request.id or request.path
        banner = (f"\n  SHAL approval required [{request.side_effect}]\n"
                  f"    {who}.{request.op}({args})\n"
                  f"  Allow this actuation? [y/N] ")
        try:
            answer = self._prompt(banner)
        except EOFError:
            return False
        return answer.strip().lower() in ("y", "yes")


# The active policy. The default is safe: prompt when interactive, deny otherwise.
# (ContextVar default must be immutable per flake8-bugbear; resolve the singleton
# in get_approver() so an unset context falls back to ConsoleApprover.)
_DEFAULT: Approver = ConsoleApprover()
_current: ContextVar[Approver | None] = ContextVar("shal_approver", default=None)


def get_approver() -> Approver:
    """The Approver the wrapper will consult for the current context."""
    return _current.get() or _DEFAULT


def set_approver(approver: Approver) -> Token:
    """Install ``approver`` as the active policy. Returns a token for ``reset``."""
    return _current.set(approver)


def reset(token: Token) -> None:
    """Undo a :func:`set_approver`, restoring the previous policy."""
    _current.reset(token)


@contextmanager
def approver(a: Approver):
    """Scope an Approver to a ``with`` block; the previous policy is restored on exit."""
    token = _current.set(a)
    try:
        yield a
    finally:
        _current.reset(token)
