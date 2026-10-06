"""The sim log (issue #312 Scope): every real exchange a run's buses made,
with its time, in the same JSON-lines format no matter whether the exchange
was driven from the `shal-arena`/`shal` CLI, MCP, or Python.

Captured at the bus layer itself (issue #457), not re-built or guessed in the
UI: `record_for` turns on `shal.log.exchange_sink` -- the opt-in hook every
bus checks after a real exchange completes -- only while a check is in
flight, rather than threading a logger through each call site by hand. That
is also why the format is uniform across modes: it is captured below the
player's surface, not inside it, so nothing the player does (CLI flag, MCP
host, raw Python) can change its shape. Off by default outside this `with`
block, same as the hook itself (#457): a plain `shal` run never logs a
payload.

A SCPI exchange (`sim_scpi`/`scpi_raw`) keeps its original two kinds,
``query``/``write`` (issue #10), told apart the same way `runner.raw_scpi`
already does -- a command ending in ``?`` is a query. Every other protocol
(Modbus-shaped messages on `sim_msg`, bytes on `sim_i2c`/`i2c_cli`) is one
kind, ``exchange``, carrying `bus_family`/`request`/`response` in its own
natural shape: never invented, only what the bus really sent and got back.

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
way it always has (on a successful exchange, nothing on a failed one --
the hook only ever fires right before a bus returns): useful detail, but
no longer what disqualification itself checks.
"""
from __future__ import annotations

import json
import math
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from shal.log import Exchange
from shal.log import exchange_sink as _exchange_sink

_SCPI_FAMILIES = frozenset({"sim_scpi", "scpi_raw"})


class SimLog:
    """One run's sim-traffic record at ``path`` (JSON lines, append-only, one
    file for the whole run — every instrument's checks land in it)."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def record_for(self, address: str) -> Iterator[None]:
        """Turn on the bus layer's opt-in exchange hook (`shal.log.
        exchange_sink`, issue #457) for the duration of one `with` block —
        every real exchange any bus makes while it is open is appended,
        tagged with ``address`` (the task-level instrument address the
        caller is checking right now, not the sim bus's own child address,
        which is internal to the harness and meaningless to the player).
        Touches the file on entry (issue #314: an attempt that reaches no
        exchange at all — an unreachable instrument refuses before the bus
        logs anything — must still leave a real log path behind, not a path
        that only sometimes exists depending on how the attempt went)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch(exist_ok=True)

        def _sink(exc: Exchange) -> None:
            if exc.bus_family in _SCPI_FAMILIES:
                # issue #10's original two kinds, kept: a command ending in
                # "?" is a query, the same rule `runner.raw_scpi` already
                # uses -- never guessed from whether the reply is empty.
                kind = "query" if str(exc.request).strip().endswith("?") else "write"
                self.append(address, kind, cmd=exc.request)
            else:
                self.append(address, "exchange", bus_family=exc.bus_family,
                           request=exc.request, response=exc.response)

        with _exchange_sink(_sink):
            yield

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
