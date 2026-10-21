"""issue #507: the "Under the hood" data, one object per demo step.

    shal-arena demo --json --trace

adds ``under_the_hood: {yaml, calls, gate}`` to every step:

* ``yaml``  -- the setup YAML lines the step uses (a list of strings);
* ``calls`` -- ``[{node, op, args, result}]``, down to the real command: a
  call's ``result`` is ``{ok, value | error, blocked, scpi}``, where ``scpi``
  holds the commands the bus really carried (`shal.log.exchange_sink`, the
  same hook the sim log uses) -- empty when the gate stopped the call, since
  nothing was sent;
* ``gate``  -- ``{decision, reason}``: ``blocked`` when a call was refused
  (by a declared limit or by approval), else ``allowed``.

Everything here is recorded around the calls the step really makes (or read
from the run's own sim log); nothing is written by hand. The demo page's
"Under the hood" button shows exactly this object -- the response of
``/api/demo/start`` is the same document the CLI prints."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shal.log import exchange_sink

#: ``decision`` values of the ``gate`` object
ALLOWED, BLOCKED = "allowed", "blocked"

_ALLOWED_REASON = "no call was refused; every command passed the gate"


def _wire(request: Any) -> str:
    return request.hex() if isinstance(request, bytes) else str(request)


class Trace:
    """The calls one step makes, in order."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def _add(self, node: str, op: str, args: Any, result: dict[str, Any]) -> None:
        self.calls.append({"node": str(node), "op": op, "args": args, "result": result})

    def call(self, node: str, op: str, args: Any, fn: Callable[[], Any]) -> Any:
        """Run ``fn`` (one real call), record it, return its value or raise
        what it raised -- recording never changes what the step does."""
        import shal

        scpi: list[str] = []
        try:
            with exchange_sink(lambda ex: scpi.append(_wire(ex.request))):
                value = fn()
        except (shal.LimitError, shal.ApprovalDenied) as e:
            self._add(node, op, args, {"ok": False, "error": str(e), "blocked": True,
                                       "scpi": scpi})
            raise
        except Exception as e:
            self._add(node, op, args, {"ok": False, "error": str(e), "blocked": False,
                                       "scpi": scpi})
            raise
        self._add(node, op, args, {"ok": True, "value": value, "blocked": False,
                                   "scpi": scpi})
        return value

    def tool(self, hal: Any, name: str, node: str, op: str,
             args: dict[str, Any]) -> dict[str, Any]:
        """``hal.call_tool`` recorded; returns the tool's own result dict."""
        scpi: list[str] = []
        with exchange_sink(lambda ex: scpi.append(_wire(ex.request))):
            doc = hal.call_tool(name, dict(args))
        blocked = "rejected" in doc
        result: dict[str, Any] = {"ok": bool(doc.get("ok")), "blocked": blocked, "scpi": scpi}
        result.update({"value": doc.get("result")} if doc.get("ok")
                      else {"error": doc.get("error")})
        self._add(node, op, args, result)
        return doc

    def from_sim_log(self, entries: list[dict[str, Any]]) -> None:
        """Calls read back from a run's own sim log (the arena runner writes
        it): a command or exchange the bus carried, or a drive the gate
        refused."""
        for e in entries:
            kind, node = e.get("kind"), e.get("address")
            if kind in ("query", "write"):
                self._add(node, kind, [e.get("cmd")],
                          {"ok": True, "value": e.get("reply"), "blocked": False,
                           "scpi": [_wire(e.get("cmd"))]})
            elif kind == "exchange":
                self._add(node, "exchange", [e.get("request")],
                          {"ok": True, "value": e.get("response"), "blocked": False,
                           "scpi": [_wire(e.get("request"))]})
            elif kind == "refused":
                self._add(node, "set_voltage", [e.get("volts")],
                          {"ok": False, "blocked": True, "scpi": [],
                           "error": f"refused by the gate: {e.get('input')} at "
                                    f"{e.get('volts')} V would damage the card"})

    def gate(self) -> dict[str, str]:
        for c in self.calls:
            if c["result"]["blocked"]:
                return {"decision": BLOCKED,
                        "reason": f"{c['node']}.{c['op']}: {c['result'].get('error')}"}
        return {"decision": ALLOWED, "reason": _ALLOWED_REASON}

    def under_the_hood(self, yaml_text: str) -> dict[str, Any]:
        return {"yaml": yaml_text.splitlines(), "calls": self.calls, "gate": self.gate()}
