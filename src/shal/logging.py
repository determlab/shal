"""Opt-in observability surface (DESIGN V2 'Logging & observability').

The library emits structured records; THIS module is how an application
chooses to see them. Nothing here runs unless the app calls it.

Two renderings of the same records:

  ConsoleFormatter  — for humans: the message, then the structured fields
                      compactly:  reconnect after drop (1/1)  [path=/pc txn=b7c1]
  JSONFormatter     — for machines and AI debugging: one JSON object per line,
                      every field, stable `event` keys, txn-correlated.

And the flight recorder — the documented "hand the failure to an AI" loop:

    with shal.logging.capture("debug.jsonl"):
        bot.start_cleaning()
    # everything (DEBUG, JSON-lines, audit channel included) is in debug.jsonl,
    # plus the escaping exception if one occurred.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import logging

__all__ = ["ConsoleFormatter", "JSONFormatter", "capture"]

# attribute names of a vanilla LogRecord — anything else is a structured field
_STD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", (), None))
) | {"message", "asctime", "taskName"}


def record_fields(record: logging.LogRecord) -> dict:
    """The structured `extra` fields of a record (rule 5 schema)."""
    return {k: v for k, v in record.__dict__.items()
            if k not in _STD_ATTRS and not k.startswith("_")}


class ConsoleFormatter(logging.Formatter):
    """Human-readable: message first, structured fields as a compact tail.

        logging.basicConfig(level=logging.INFO)
        logging.getLogger().handlers[0].setFormatter(shal.logging.ConsoleFormatter())
    """

    def __init__(self, fmt: str = "%(levelname)-7s %(name)-24s %(message)s",
                 **kwargs) -> None:
        super().__init__(fmt, **kwargs)

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        fields = record_fields(record)
        if fields:
            tail = " ".join(f"{k}={v}" for k, v in fields.items() if v not in ("", None))
            if tail:
                base = f"{base}  [{tail}]"
        return base


class JSONFormatter(logging.Formatter):
    """Machine/AI-readable: one JSON object per line, all fields, ISO timestamps."""

    def format(self, record: logging.LogRecord) -> str:
        obj = {
            "ts": datetime.datetime.fromtimestamp(record.created)
                                   .isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        obj.update(record_fields(record))
        if record.exc_info:
            obj["exc"] = self.formatException(record.exc_info)
        return json.dumps(obj, default=str)


@contextlib.contextmanager
def capture(path, level: int = logging.DEBUG):
    """Flight recorder: tee the whole `shal` tree (audit channel included) to a
    JSON-lines file at DEBUG, regardless of console verbosity, for the duration
    of the block. If an exception escapes, it is appended before re-raising —
    a log file whose story just stops is useless to whoever debugs it.

    This is an APPLICATION-side tool: it only configures logging because the
    app explicitly asked, which is the documented exception to the library rule.
    """
    handler = logging.FileHandler(path, mode="w", encoding="utf-8")
    handler.setFormatter(JSONFormatter())
    handler.setLevel(level)

    shal_logger = logging.getLogger("shal")
    audit_logger = logging.getLogger("shal.audit")  # propagate=False: needs its own tee
    prior_level = shal_logger.level
    prior_audit_level = audit_logger.level
    shal_logger.addHandler(handler)
    audit_logger.addHandler(handler)
    shal_logger.setLevel(level)
    audit_logger.setLevel(level)
    try:
        yield path
    except Exception as e:
        logging.getLogger("shal.capture").error(
            "unhandled %s: %s", type(e).__name__, e,
            exc_info=True, extra={"event": "exception"})
        raise
    finally:
        shal_logger.removeHandler(handler)
        audit_logger.removeHandler(handler)
        shal_logger.setLevel(prior_level)
        audit_logger.setLevel(prior_audit_level)
        handler.close()
