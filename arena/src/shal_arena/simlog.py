"""The sim log (issue #312 Scope): every SCPI command the sim bus received
during a run, with its time, in the same JSON-lines format no matter whether
the exchange was driven from the `shal-arena`/`shal` CLI, MCP, or Python.

It is captured at the bus's own structured record — ``shal,sim-scpi`` already
logs every exchange as ``event="exchange"`` on the ``shal.bus.sim_scpi``
logger (issue #10) — by attaching a `logging.Handler` only while a check is
in flight, rather than threading a logger through each call site by hand.
That is also why the format is uniform across modes: it is captured below
the player's surface, not inside it, so nothing the player does (CLI flag,
MCP host, raw Python) can change its shape.

"An answer with no matching measurement in the log is disqualified"
(`runner.answer`) reads this log back for a ``measure`` entry at a probe
instrument's address — proof the player made the attempt, not just typed an
answer. ``measure`` is written by `mark_measured`, unconditionally, BEFORE
the read — the one deliberately neutral entry in this file: same shape
whether the read that follows succeeds or raises, so its presence alone
never tells anyone which fault is active (CTO review on #322, round 2: an
earlier version disqualified on a missing ``query`` instead, which meant the
`open` fault — unreachable by construction — could never be logged as
measured, so a correctly-reasoned `open` answer was always disqualified).

The bus's own structured record is still captured on top of that, the same
way it always has (``query``/``write`` on a successful exchange, nothing on
a failed one — issue #10's own logging, not anything built for this file):
useful detail, but no longer what disqualification itself checks.
"""
from __future__ import annotations

import json
import logging
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_LOGGER_NAME = "shal.bus.sim_scpi"


class _JsonLinesHandler(logging.Handler):
    """Appends one line per ``exchange`` record, tagged with the task-level
    instrument ``address`` the caller is checking right now (not the sim
    bus's own child address, which is internal to the harness and meaningless
    to the player)."""

    def __init__(self, path: Path, address: str) -> None:
        super().__init__(level=logging.DEBUG)
        self._path = path
        self._address = address

    def emit(self, record: logging.LogRecord) -> None:
        if getattr(record, "event", None) != "exchange":
            return
        kind, cmd = record.args  # ("query"|"write", the scpi command text)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "address": self._address,
            "kind": kind,
            "cmd": cmd,
        }
        with self._path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


class SimLog:
    """One run's sim-traffic record at ``path`` (JSON lines, append-only, one
    file for the whole run — every instrument's checks land in it)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def record_for(self, address: str) -> Iterator[None]:
        """Attach the recorder for the duration of one `with` block — every
        SCPI exchange the sim bus handles while it is open is appended,
        tagged with ``address``. Touches the file on entry (issue #314: an
        attempt that reaches no exchange at all — an unreachable instrument
        refuses before the bus logs anything — must still leave a real log
        path behind, not a path that only sometimes exists depending on how
        the attempt went)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)
        logger = logging.getLogger(_LOGGER_NAME)
        prev_level = logger.level
        handler = _JsonLinesHandler(self.path, address)
        logger.setLevel(logging.DEBUG)
        logger.addHandler(handler)
        try:
            yield
        finally:
            logger.removeHandler(handler)
            logger.setLevel(prev_level)

    def mark_measured(self, address: str) -> None:
        """A neutral marker that a measurement was attempted at ``address``
        — written BEFORE the read, regardless of what the read then does.
        No result, no error, nothing fault-specific: just that the attempt
        happened and when. This is what `answer`'s disqualification check
        looks for (CTO review on #322, round 2) — not whether the read
        itself succeeded, which `has_query` still tells you, for anyone who
        wants it, but which must not gate credit for a correct answer to a
        fault that makes every read fail by design (`open`)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "address": address, "kind": "measure"}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def mark_reading(self, address: str, value: float, unit: str | None) -> None:
        """issue #406 follow-up (the demo page): the actual number a
        successful read returned, logged AFTER `mark_measured`'s neutral
        marker and only on success -- never written for a failed read
        (`take_measurement`'s own docstring: a failure is "never written to
        the sim log ... beyond that one neutral marker"), so this entry's
        mere presence already tells a reader the read succeeded, without
        needing to say anything about why one might be missing.

        CTO review on #427: `value` is coerced to `float` here, not trusted
        from the caller -- a driver an agent wrote returns whatever its own
        code computes, and this is the one place that decides what counts
        as "a reading" at all. Anything that is not a bare, finite number
        (e.g. an HTML string smuggled in to run in the page later, or a
        `nan`/`inf` that would write invalid JSON `NaN`/`Infinity` and
        break `JSON.parse` on every later read of this file) raises
        instead of being logged."""
        v = float(value)
        if not math.isfinite(v):
            raise ValueError(f"reading {v!r} for {address!r} is not finite")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "address": address, "kind": "reading", "value": v, "unit": unit}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def append(self, address: str, kind: str, **extra: Any) -> None:
        """A free-form entry, same JSON-lines shape as every other line in
        this file (issue relay-rail: `runner.call_op`'s own record of a
        generic `call` — the bus-log capture `record_for` attaches only
        covers `shal,sim-scpi`, so a `shal,sim-msg`/`shal,sim-i2c` exchange
        needs its own line written directly, the same way `mark_measured`
        and `CardSim._log` already do)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "address": str(address), "kind": kind, **extra}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def has_measure(self, address: str) -> bool:
        """A measurement was attempted at ``address`` — what `answer`'s
        disqualification check counts, regardless of whether the read that
        followed succeeded."""
        return any(e["kind"] == "measure" and e["address"] == str(address)
                   for e in self.entries())

    def has_query(self, address: str) -> bool:
        """A genuine, successful reading at ``address`` (the bus's own
        record) — informational; not what disqualification checks."""
        return any(e["kind"] == "query" and e["address"] == str(address)
                   for e in self.entries())
