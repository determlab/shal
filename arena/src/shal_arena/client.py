"""The client side of ``--server`` (issue #486): the run commands as requests
to a ``shal-arena serve`` server.

Nothing is kept here -- no seed, no state file, no ``cli.jsonl``; each
command returns what the server (or, for ``check``, the local fault-free
copy of the case) gives it. ``run`` returns a per-run ``token``; every other
call sends it back.

The driver never leaves this process. ``measure`` and ``call`` import the
agent's ``driver.py`` here and bind it to a ``shal,remote-bus`` node (the
server URL, the run's token and one session), so each ``exchange`` / ``txn``
the driver makes is a request to the server's sim bus. ``check`` never
contacts the server: it runs the conformance harness against the case's
static, fault-free harness. A reading the driver took is reported at the end
for display and is never what scores the run -- the server scores from the
bus traffic it saw.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from shal import registry
from shal.conformance import check_driver as _conformance_check_driver
from shal.errors import HopError, HopTimeout

from . import errors as _errors
from . import remote_bus  # noqa: F401 - registers `shal,remote-bus`
from .cases import CASES, resolve_case
from .errors import ArenaError, CheckCouldNotRun, MeasurementFailed
from .runner import _coerce_args, _import_driver_file, _zero_arg_read_op

_TIMEOUT_S = 30.0


class ServerError(ArenaError):
    """The server refused or failed a request: its own message and fix, with
    the exit code the same error has locally (403 -> 2 via `UnknownRun`-style
    named types, otherwise 1)."""

    def __init__(self, message: str, *, fix: str, remote_type: str = "ServerError") -> None:
        super().__init__(message, fix=fix)
        self.remote_type = remote_type
        named = getattr(_errors, remote_type, None)
        if isinstance(named, type) and issubclass(named, ArenaError):
            self.exit_code = named.exit_code
        elif remote_type == "_Forbidden":
            self.exit_code = 2

    def to_dict(self) -> dict:
        return {"type": self.remote_type, "message": self.message, "fix": self.fix}


def post(server: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST ``body`` as JSON; the reply document, or a `ServerError` carrying
    the server's own message and fix."""
    body = {k: v for k, v in body.items() if v is not None}
    req = urllib.request.Request(server.rstrip("/") + path, method="POST",
                                 data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())["error"]
            raise ServerError(err["message"], fix=err["fix"],
                              remote_type=err.get("type", "ServerError")) from None
        except (ValueError, KeyError, TypeError):
            raise ServerError(f"{server} answered HTTP {e.code}",
                              fix="check --server is a `shal-arena serve` URL") from None
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise ServerError(f"cannot reach {server}: {getattr(e, 'reason', e)}",
                          fix="start one with `shal-arena serve --json` and pass its "
                              "url as --server") from None


def run(server: str, task: str, *, seed: int | None, note: str | None) -> dict[str, Any]:
    return post(server, "/api/play/start", {"task": task, "seed": seed, "note": note})


def drive(server: str, run_id: str, token: str | None, address: str, volts: float, *,
          note: str | None) -> dict[str, Any]:
    return post(server, "/api/play/drive", {"run_id": run_id, "token": token,
                                            "address": address, "volts": volts, "note": note})


def answer(server: str, run_id: str, token: str | None, value: str, *,
           note: str | None) -> dict[str, Any]:
    return post(server, "/api/play/answer", {"run_id": run_id, "token": token,
                                             "value": value, "note": note})


def check(run_id: str, instrument: str, driver_path: str | Path) -> dict[str, Any]:
    """`check-driver --server`: the conformance harness, here, against the
    case's own fault-free harness. ``instrument`` is the case name
    (``dmm``), or any address -- then the case is the one whose compatible
    the imported driver registered. Never contacts the server."""
    _import_driver_file(driver_path)
    if instrument in CASES:
        case = resolve_case(instrument)
    else:
        found = [c for c in CASES if _registered_by(CASES[c].compatible, Path(driver_path).stem)]
        if len(found) != 1:
            raise CheckCouldNotRun(
                f"cannot tell which case {instrument!r} is from this driver",
                fix=f"pass the case name as the instrument: {', '.join(sorted(CASES))}")
        case = resolve_case(found[0])
    try:
        report = _conformance_check_driver(case.compatible, topology=str(case.harness_topology))
    except Exception as e:  # noqa: BLE001 - the check itself could not run
        raise CheckCouldNotRun(f"check could not run: {type(e).__name__}: {e}",
                               fix="fix the error above in your driver.py and run the "
                                   "check again") from e
    return {"ok": True, "side_effect": "none", "run_id": run_id, "address": instrument,
            "case": case.name, "passed": report.ok, "problems": report.problems,
            "warnings": report.warnings}


def _registered_by(compatible: str, module: str) -> bool:
    """True when ``compatible`` resolves to a class defined in the driver
    file just imported (`_import_driver_file` names its module by file stem)."""
    try:
        return registry.resolve(compatible).__module__ == module
    except Exception:  # noqa: BLE001 - not registered (or not unique): not this case
        return False


def _begin(server: str, run_id: str, token: str | None, address: str, action: str,
           at: str | None) -> dict[str, Any]:
    return post(server, "/api/remote/begin", {"run_id": run_id, "token": token,
                                              "address": address, "action": action, "at": at})


def _topology(server: str, run_id: str, token: str | None, meta: dict[str, Any]) -> dict:
    """The agent's driver bound to the server's bus: one `shal,remote-bus`
    root (the URL, the run token, the session) with the case's node under it."""
    return {"shal_version": 1, "root": {"bus": {
        "id": "bus", "driver": "shal,remote-bus", "address": server.rstrip("/"),
        "config": {"run_id": run_id, "token": token or "none", "session": meta["session"]},
        "children": {"unit": {"id": "unit", "driver": meta["compatible"],
                              "address": meta["child_address"]}}}}}


def _bound_driver(hal: Any, cls: type, case_name: str):
    node = next((n for root in hal._roots for n in root.walk() if isinstance(n.driver, cls)),
                None)
    if node is None:
        raise CheckCouldNotRun(
            f"{case_name}: no node bound to your driver",
            fix="your driver.py must set compatible to the case's and register itself")
    return node


def measure(server: str, run_id: str, token: str | None, address: str,
            driver_path: str | Path, *, at: str | None, note: str | None) -> dict[str, Any]:
    from shal.hal import load as _load

    meta = _begin(server, run_id, token, address, "measure", at)
    _import_driver_file(driver_path)
    cls = _resolve_driver(meta)
    read_op = _zero_arg_read_op(cls)
    if read_op is None:
        raise CheckCouldNotRun(
            f"{meta['compatible']}: no zero-argument read op to measure with",
            fix="this case has no op with side_effect='none' and no required "
                "params -- measuring it needs a different driver shape")
    finish = {"run_id": run_id, "token": token, "session": meta["session"], "note": note}
    try:
        with _load(_topology(server, run_id, token, meta)) as hal:
            node = _bound_driver(hal, cls, meta["case"])
            reading = getattr(node.driver, read_op)()
        unit = (getattr(cls.capability_ops()[read_op], "__shal_op__", {}) or {}).get("unit")
    except CheckCouldNotRun:
        raise
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call
        cause = "transport" if isinstance(e, (HopError, HopTimeout)) else "driver"
        post(server, "/api/remote/finish", {**finish, "failed": True, "cause": cause})
        raise MeasurementFailed(
            f"{read_op} raised {type(e).__name__}: {e}",
            fix="the instrument did not answer this call -- if that's unexpected, "
                "check your driver.py's handling of the case's SCPI dialect") from e
    return post(server, "/api/remote/finish", {**finish, "op": read_op, "reading": reading,
                                               "unit": unit})


def call(server: str, run_id: str, token: str | None, address: str, driver_path: str | Path,
         op_name: str, args: list[str], *, at: str | None, note: str | None) -> dict[str, Any]:
    from shal.driver import inferred_side_effect
    from shal.hal import load as _load

    meta = _begin(server, run_id, token, address, "call", at)
    _import_driver_file(driver_path)
    cls = _resolve_driver(meta)
    ops = cls.capability_ops()
    if op_name not in ops:
        known = ", ".join(sorted(ops)) or "(none)"
        raise CheckCouldNotRun(f"{meta['compatible']} has no op {op_name!r}",
                               fix=f"call one of this driver's ops: {known}")
    kwargs = _coerce_args(ops[op_name], args)
    side_effect = inferred_side_effect(ops[op_name])
    if meta["drives"] and not meta["power_switch"] and side_effect != "none":
        raise CheckCouldNotRun(
            f"{address}: {op_name} changes this instrument's own output; only "
            "`shal-arena drive` carries the card's damage gate for a 'drives' instrument",
            fix=f"use `shal-arena drive <run> {address} <volts>` instead of call")
    finish = {"run_id": run_id, "token": token, "session": meta["session"], "note": note,
              "op": op_name, "args": args}
    try:
        with _load(_topology(server, run_id, token, meta)) as hal:
            node = _bound_driver(hal, cls, meta["case"])
            result = hal.call_tool(f"{node.id or 'unit'}__{op_name}", kwargs)
    except CheckCouldNotRun:
        raise
    except Exception as e:  # noqa: BLE001 - a live answer to THIS call
        post(server, "/api/remote/finish", {**finish, "failed": True})
        raise MeasurementFailed(
            f"{op_name} raised {type(e).__name__}: {e}",
            fix="the call failed -- check your driver.py's handling of this op") from e
    post(server, "/api/remote/finish", {**finish, "ok": bool(result.get("ok"))})
    out = {"ok": bool(result.get("ok")), "side_effect": side_effect, "run_id": run_id,
           "address": meta["address"], "op": op_name, "args": args}
    out.update({k: v for k, v in result.items() if k != "ok"})
    return out


def _resolve_driver(meta: dict[str, Any]) -> type:
    try:
        return registry.resolve(meta["compatible"])
    except Exception as e:  # noqa: BLE001 - a bad/missing registration is a named failure
        raise CheckCouldNotRun(
            f"driver.py does not register {meta['compatible']!r}: {type(e).__name__}: {e}",
            fix=f"make sure your driver.py sets compatible = {meta['compatible']!r} and "
                "calls registry.register(...)") from e
