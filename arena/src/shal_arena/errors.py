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


class CheckCouldNotRun(ArenaError):
    """The ADK-style driver check could not even run: the driver file does not
    exist, does not import, or raised before `conformance.check_driver` could
    produce a report. Exit 3, same family as `shal check`'s own
    `CheckCouldNotRun` — a crash, not "the driver failed its check"."""

    exit_code = 3
