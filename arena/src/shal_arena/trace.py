"""issue #507: "Under the hood" -- what one demo step really did.

``shal-arena demo --json --trace`` adds one key to every step:
``under_the_hood: {yaml, calls, gate}``.

* ``yaml``  -- the setup YAML lines the step uses (a list of strings).
* ``calls`` -- ``[{node, op, args, result}]`` in the order they ran: each op
  the step called, then, under it, the real bus exchanges it made (the SCPI
  command and its reply), caught at the bus layer by ``shal.log.exchange_sink``
  -- recorded, never composed.
* ``gate``  -- ``{decision, reason}``: ``allowed`` (nothing needed approval),
  ``approved`` (a gated op was approved), or ``blocked`` (refused, nothing sent).

The demo page's "Under the hood" button shows exactly this object."""
from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from typing import Any

_SCPI_FAMILIES = frozenset({"sim_scpi", "scpi_raw"})
_RANK = {"allowed": 0, "approved": 1, "blocked": 2}
_DEFAULT_GATE = {"decision": "allowed",
                 "reason": "no op in this step needed approval: reads and writes run at once"}


class Trace:
    def __init__(self, yaml_lines: list[str] | tuple[str, ...] = ()) -> None:
        self.yaml: list[str] = list(yaml_lines)
        self.calls: list[dict[str, Any]] = []
        self._gate: dict[str, str] = dict(_DEFAULT_GATE)

    @contextlib.contextmanager
    def capture(self) -> Iterator[None]:
        """Record every real bus exchange made inside the block."""
        from shal.log import Exchange, exchange_sink

        def _sink(exc: Exchange) -> None:
            if exc.bus_family in _SCPI_FAMILIES:
                self.calls.append({"node": exc.path, "op": "scpi",
                                   "args": {"cmd": str(exc.request)},
                                   "result": str(exc.response)})
            else:
                self.calls.append({"node": exc.path, "op": "exchange",
                                   "args": {"bus": exc.bus_family, "request": str(exc.request)},
                                   "result": str(exc.response)})

        with exchange_sink(_sink):
            yield

    def call(self, node: str, op: str, args: dict[str, Any], fn: Callable[[], Any]) -> Any:
        """Run ``fn`` and record it as one call; a raised error is recorded as
        the call's result and re-raised unchanged."""
        entry: dict[str, Any] = {"node": str(node), "op": op, "args": dict(args), "result": None}
        self.calls.append(entry)
        try:
            result = fn()
        except Exception as e:
            entry["result"] = {"error": f"{type(e).__name__}: {e}"}
            raise
        entry["result"] = result
        return result

    def call_logged(self, node: str, op: str, args: dict[str, Any], fn: Callable[[], Any],
                    sim_log: Any) -> Any:
        """Like `call`, for a runner call: the runner keeps its own bus hook
        on, so the exchanges it made are read back from the run's sim log (the
        bus layer's own record) and listed under the call."""
        before = len(sim_log.entries())
        try:
            return self.call(node, op, args, fn)
        finally:
            for e in sim_log.entries()[before:]:
                if e["kind"] in ("query", "write"):
                    self.calls.append({"node": e["address"], "op": "scpi",
                                       "args": {"cmd": e.get("cmd")},
                                       "result": e.get("reply")})
                elif e["kind"] == "exchange":
                    self.calls.append({"node": e["address"], "op": "exchange",
                                       "args": {"bus": e.get("bus_family"),
                                                "request": e.get("request")},
                                       "result": e.get("response")})

    def gate(self, decision: str, reason: str) -> None:
        """Set the step's gate verdict; the stricter one wins (blocked over
        approved over allowed), so a later read never hides an earlier stop."""
        if _RANK[decision] >= _RANK[self._gate["decision"]]:
            self._gate = {"decision": decision, "reason": reason}

    def gate_from_tool(self, called: dict[str, Any]) -> None:
        """The gate verdict of one ``hal.call_tool`` result."""
        if called.get("rejected"):
            self.gate("blocked", f"{called['rejected']}: {called.get('error')} -- not sent")

    def export(self) -> dict[str, Any]:
        return {"yaml": list(self.yaml), "calls": list(self.calls), "gate": dict(self._gate)}
