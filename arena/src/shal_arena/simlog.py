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
(`runner.answer`) reads this log back for at least one ``query`` at a probe
instrument's address — proof the player actually measured, not just typed an
answer. The log carries ONLY what the bus's own structured record produces:
a successful exchange logs ``query``/``write`` as always; a failed one (the
`open` fault, or a driver bug) logs nothing at all here (CTO review on #322:
an earlier version wrote a fixed ``"attempt"``/``"unreachable"`` entry for
every failure, and since a correct driver only ever fails to read on the
`open` fault, that entry — regardless of its exact wording — named the fault
as surely as writing its id would have). A failed measurement is reported
live to whoever just tried it (`runner.take_measurement`'s own return value
or raised error), never written here.
"""
from __future__ import annotations

import json
import logging
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
        tagged with ``address``."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
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

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in
                self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def has_query(self, address: str) -> bool:
        """A genuine, successful reading at ``address`` — the only thing
        `answer`'s disqualification check counts as a measurement."""
        return any(e["kind"] == "query" and e["address"] == str(address)
                   for e in self.entries())
