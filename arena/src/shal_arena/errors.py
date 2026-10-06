"""shal-arena's exception hierarchy.

Every arena command is driven by an agent (issue #310), so every error carries
a ``fix``: the next command or edit that resolves it. ``--json`` surfaces it as
``{"ok": false, "error": {"type": ..., "message": ..., "fix": ...}}`` — one
shape for every command, mirroring `shal`'s own ``--json`` error object
(AGENTS.md).
"""
from __future__ import annotations


class ArenaError(Exception):
    """Base for every shal-arena error. ``fix`` is never empty."""

    exit_code = 1

    def __init__(self, message: str, *, fix: str) -> None:
        super().__init__(message)
        self.message = message
        self.fix = fix

    def to_dict(self) -> dict:
        return {"type": type(self).__name__, "message": self.message, "fix": self.fix}


class TaskFormatError(ArenaError):
    """``task.yaml`` or ``card.yaml`` is malformed: an unknown or missing key, a
    wrong format version, a ``case`` that isn't packaged, wiring that names no
    card input/test point, or ``question.answer.values`` that disagrees with
    the card's fault catalogue (CTO ruling, issue #310). Exit 2 — the file is
    the problem, not the run."""

    exit_code = 2


class UnknownRun(ArenaError):
    """``answer``/``check`` named a run id this state dir never started, or one
    already closed. Exit 2: a usage mistake, not a crash."""

    exit_code = 2


class RunClosed(UnknownRun):
    """Input (`check`, `measure`, `drive`) sent to a run that `answer` already
    closed (issue #325): refused, nothing sent, card state unchanged."""


class CheckCouldNotRun(ArenaError):
    """The ADK-style driver check could not even run: the driver file does not
    exist, does not import, or raised before `conformance.check_driver` could
    produce a report. Exit 3, same family as `shal check`'s own
    `CheckCouldNotRun` — a crash, not "the driver failed its check"."""

    exit_code = 3


class MeasurementFailed(ArenaError):
    """``measure`` (issue #312) bound the player's own driver and called its
    read op, but the exchange itself raised — same exit family as ``shal
    call``'s own op-failed outcome (AGENTS.md: "1 the op failed"), not a
    crash in the check machinery. The message is whatever the raised
    exception says (a live answer to the player's own action, not something
    written to a file) — this is never persisted, so it carries no "must
    never name the fault" obligation the sim log has."""

    exit_code = 1


class TooFewRuns(ArenaError):
    """``bench`` (issue #314 Scope: "at least 10 runs per side") was asked for
    fewer runs than that — refused before either side plays a single run.
    Exit 2: a usage mistake, not a crash."""

    exit_code = 2


class LockTimeout(ArenaError):
    """issue #436 CTO review: the per-run state file lock (``store.py``'s
    ``_locked_state_file``) waited past its own deadline — another process
    is genuinely stuck holding it (crashed mid-write, or a debugger
    attached to it), not just slow. Exit 1: a crash, not a usage
    mistake — a healthy run never hits this."""

    exit_code = 1
