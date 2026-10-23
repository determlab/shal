"""issue #507: ``under_the_hood`` -- what one demo step really did, as data.

    shal-arena demo --json --trace

adds to every step ``under_the_hood: {yaml, calls, gate}``:

- ``yaml``: the setup-YAML lines the step uses, ``[{n, text, node}]`` (``n`` is
  the line number in the whole file, ``node`` the device the line belongs to,
  ``null`` for a shared line such as a bus header);
- ``calls``: ``[{node, op, args, result}]`` in the order they ran. ``result``
  is what the call really returned, plus ``sent`` (false: refused, nothing
  went out) and ``wire`` (the real bus commands and replies, e.g.
  ``"MEAS:VOLT:DC? -> 3.30"``);
- ``gate``: ``{decision, reason}``; ``decision`` is ``blocked`` (a command
  was refused and not sent), ``allowed`` or ``none`` (the step made no call).

The demo page shows this object as it is; it keeps no second copy. Every
number here comes from the run: the sim bus's own exchanges
(`shal.log.exchange_sink`), the gate's own refusal, or the run's sim log."""
from __future__ import annotations

import re
from typing import Any

import shal
from shal.log import Exchange, exchange_sink

_KEY = re.compile(r"^(\s*)([^\s#:-][^:]*):")
_SCALAR = re.compile(r"^\s*[^\s#:-][^:]*:\s+\S")


def annotate_yaml(text: str, nodes: set[str]) -> list[dict[str, Any]]:
    """Every non-blank, non-comment line of a nested topology YAML, with the
    device key (one of ``nodes``) it sits under. ``_anc`` holds the line
    numbers of the keys above that device (its bus), kept so `Hood` can show
    the path down to a used line."""
    out: list[dict[str, Any]] = []
    stack: list[tuple[int, str, int]] = []  # (indent, key, line number)
    for n, raw in enumerate(text.splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        m = _KEY.match(raw)
        if m:
            indent = len(m.group(1))
            while stack and stack[-1][0] >= indent:
                stack.pop()
            stack.append((indent, m.group(2).strip(), n))
        keys = [k for _, k, _ in stack]
        node = next((k for k in reversed(keys) if k in nodes), None)
        anc = [ln for _, _, ln in stack[:keys.index(node)]] if node else []
        # a bare `key: value` line of a bus (its driver, its address) belongs
        # with the bus when a device under that bus is shown
        par = stack[-2][2] if m and not node and len(stack) > 1 and _SCALAR.match(raw) else None
        out.append({"n": n, "text": raw, "node": node, "_anc": anc, "_par": par})
    return out


def annotate_task_yaml(text: str) -> list[dict[str, Any]]:
    """The ``instruments:`` list of an arena task YAML: each ``- case: ...``
    item is one block, its node the item's ``address:``."""
    out: list[dict[str, Any]] = []
    header: int | None = None
    block: list[dict[str, Any]] = []

    def close() -> None:
        node = next((m.group(1) for e in block
                     if (m := re.match(r"^\s*(?:- )?address:\s*(\S+)", e["text"]))), None)
        for e in block:
            e["node"], e["_anc"] = node, [header]
        out.extend(block)
        block.clear()

    for n, raw in enumerate(text.splitlines(), 1):
        if raw.startswith("instruments:"):
            header = n
            out.append({"n": n, "text": raw, "node": None, "_anc": []})
        elif header is not None and (raw.startswith(" ") or raw.lstrip().startswith("-")):
            if raw.lstrip().startswith("- ") and block:
                close()
            block.append({"n": n, "text": raw})
        elif header is not None:
            close()
            header = None
    if block:
        close()
    return out


class Hood:
    """Collects one step's calls and builds its ``under_the_hood`` object."""

    def __init__(self) -> None:
        self._yaml: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        self.allowed_reason = "every call was sent: reads run at once, writes stayed inside limits"
        self.gate: dict[str, str] | None = None

    def set_yaml(self, entries: list[dict[str, Any]]) -> None:
        self._yaml = entries

    def add(self, node: str, op: str, args: Any, result: dict[str, Any]) -> None:
        self.calls.append({"node": node, "op": op, "args": args, "result": result})

    def run(self, node: str, op: str, args: dict[str, Any], fn: Any) -> Any:
        """Run ``fn`` (one device call) and record it with the bus commands it
        really made. A refusal by the gate is recorded as not sent."""
        wire: list[str] = []

        def sink(e: Exchange) -> None:
            wire.append(f"{e.request} -> {e.response}")

        try:
            with exchange_sink(sink):
                value = fn()
        except (shal.LimitError, shal.ApprovalDenied) as e:
            why = "limits" if isinstance(e, shal.LimitError) else "approval"
            self.add(node, op, args, {"ok": False, "sent": False, "rejected": why,
                                      "error": str(e), "wire": wire})
            raise
        except Exception as e:
            self.add(node, op, args, {"ok": False, "sent": bool(wire), "error": str(e),
                                      "wire": wire})
            raise
        self.add(node, op, args, {"ok": True, "sent": True, "result": value, "wire": wire})
        return value

    def add_tool_call(self, node: str, op: str, args: dict[str, Any], res: dict[str, Any],
                      wire: list[str]) -> None:
        """A `hal.call_tool` result, as it came back."""
        self.add(node, op, args, {**res, "sent": "rejected" not in res, "wire": wire})

    def add_simlog(self, entries: list[dict[str, Any]]) -> None:
        """The bus traffic of an arena run, from its own sim log."""
        for e in entries:
            if "volts" in e:  # a drive of the card's input, as the card sim logged it
                args = {"input": e.get("input"), "volts": e["volts"]}
                if e["kind"] == "refused":
                    self.add(e["address"], "drive", args, {
                        "ok": False, "sent": False, "rejected": "approval", "wire": [],
                        "error": "the gate refused this drive"})
                else:
                    self.add(e["address"], "drive", args, {
                        "ok": True, "sent": True, "result": e["kind"],
                        "wire": [f"{e.get('input')} <- {e['volts']} V"]})
            elif e["kind"] in ("query", "write") and "cmd" in e:
                self.add(e["address"], e["kind"], [e["cmd"]],
                         {"ok": True, "sent": True, "result": e.get("reply"),
                          "wire": [f"{e['cmd']} -> {e.get('reply', '')}"]})
            elif e["kind"] == "exchange":
                self.add(e["address"], "exchange", [e.get("request")],
                         {"ok": True, "sent": True, "result": e.get("response"),
                          "wire": [f"{e.get('request')} -> {e.get('response')}"]})

    def build(self) -> dict[str, Any]:
        used = {c["node"] for c in self.calls}
        keep: set[int] = set()
        for e in self._yaml:
            if not used or e["node"] in used:
                keep.add(e["n"])
                keep.update(e["_anc"])
        keep.update(e["n"] for e in self._yaml if e.get("_par") in keep and used)
        lines = [{"n": e["n"], "text": e["text"], "node": e["node"]}
                 for e in self._yaml if e["n"] in keep]
        blocked = [c for c in self.calls if c["result"].get("rejected")]
        failed = [c for c in self.calls if c["result"].get("ok") is False and c not in blocked]
        if self.gate is not None:
            gate = self.gate
        elif blocked:
            c = blocked[0]
            gate = {"decision": "blocked",
                    "reason": f"{c['node']}.{c['op']}: refused by the gate "
                              f"({c['result'].get('rejected')}): {c['result'].get('error')}; "
                              "nothing was sent"}
        elif failed:
            c = failed[0]
            gate = {"decision": "allowed",
                    "reason": f"the gate stopped nothing; {c['node']}.{c['op']} failed on the "
                              f"bus: {c['result'].get('error')}"}
        elif self.calls:
            gate = {"decision": "allowed", "reason": self.allowed_reason}
        else:
            gate = {"decision": "none", "reason": "this step makes no device call"}
        return {"yaml": lines, "calls": self.calls, "gate": gate}
