"""Lookup API + lifecycle (DESIGN V2 'Lookup API' / 'Lifecycle')."""
from __future__ import annotations

import inspect
import logging
import re
import typing

from .errors import Error, HopError, LoadError
from .loader import load_tree
from .node import Node
from .transport import Transport

logger = logging.getLogger("shal.loader")

_JSON_TYPES = {int: "integer", float: "number", str: "string", bool: "boolean"}
_NAME_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


class Hal:
    def __init__(self, roots: list[Node], ids: dict[str, Node]) -> None:
        self._roots = roots
        self._ids = ids
        self._closed = False
        self._tool_idx: dict[str, tuple[Node, str]] | None = None

    # -- lookup (topology immutable after load -> lock-free) -----------------
    def get_device(self, key: str | None = None, *,
                   id: str | None = None, path: str | None = None):
        if sum(x is not None for x in (key, id, path)) != 1:
            raise LoadError("get_device takes exactly one of: positional key, id=, path=")
        if key is not None:  # DECISIONS v2.1 #2: leading '/' = path, else id
            if key.startswith("/"):
                path = key
            else:
                id = key
        node = self._ids.get(id) if id is not None else self._by_path(path)  # type: ignore[arg-type]
        if node is None:
            raise LoadError(f"no device with {'id' if id else 'path'} "
                            f"'{id if id is not None else path}'")
        if node.driver is None:
            raise LoadError(f"{node.path} has no driver (channel node?)")
        return node.driver

    def get_node(self, id: str) -> Node:
        node = self._ids.get(id)
        if node is None:
            raise LoadError(f"no node with id '{id}'")
        return node

    # -- LLM tool surface (DESIGN V2 'agent bus') ----------------------------
    def _tool_index(self) -> dict[str, tuple[Node, str]]:
        """tool name -> (device node, op name). Built once from the bound tree."""
        if getattr(self, "_tool_idx", None) is None:
            idx: dict[str, tuple[Node, str]] = {}
            for root in self._roots:
                for node in root.walk():
                    drv = node.driver
                    # devices are agent-callable; a bus provides transport, not
                    # capabilities — exclude it (same rule as the audit channel)
                    if drv is None or isinstance(drv, Transport):
                        continue
                    handle = node.id or _NAME_SAFE.sub("_", node.path.lstrip("/"))
                    for opname in type(drv).capability_ops():
                        idx[f"{handle}__{opname}"] = (node, opname)
            self._tool_idx = idx
        return self._tool_idx

    def tool_schemas(self) -> list[dict]:
        """Anthropic tool-use definitions ({name, description, input_schema}) for
        every capability op on every device — drive a SHAL tree from an LLM."""
        out = []
        for name, (node, opname) in self._tool_index().items():
            fn = type(node.driver).capability_ops()[opname]
            out.append({
                "name": name,
                "description": _describe(node, opname, fn),
                "input_schema": _params_schema(fn),
            })
        return out

    def tool_catalog(self) -> list[dict]:
        """Richer per-tool facts for policy/gating: side_effect + idempotency.
        Pair with tool_schemas() — the harness gates writes/actuators, not reads."""
        out = []
        for name, (node, opname) in self._tool_index().items():
            fn = type(node.driver).capability_ops()[opname]
            out.append({"name": name, "device": node.id or node.path,
                        "op": opname, **_effect(fn)})
        return out

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        """Dispatch a tool call by name. Returns {"ok": True, "result": ...} or,
        on failure, {"ok": False, "error": ..., "delivered": ...} — a
        delivery-unknown write is reported, never silently retried (decision 6)."""
        idx = self._tool_index()
        if name not in idx:
            raise LoadError(f"no tool '{name}' (see tool_schemas())")
        node, opname = idx[name]
        method = getattr(node.driver, opname)
        try:
            result = method(**(arguments or {}))
        except HopError as e:
            return {"ok": False, "error": str(e), "delivered": e.delivered}
        except Error as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "result": result}

    def _by_path(self, path: str) -> Node | None:
        parts = [p for p in path.split("/") if p]
        if not parts:
            return None
        node = next((r for r in self._roots if r.name == parts[0]), None)
        for part in parts[1:]:
            if node is None:
                return None
            node = node.children.get(part)
        return node

    # -- lifecycle ------------------------------------------------------------
    def close(self) -> None:
        """Teardown leaf->root. Deterministic on exit and on exceptions."""
        if self._closed:
            return
        self._closed = True
        for root in self._roots:
            self._close_subtree(root, set())
        logger.info("teardown complete", extra={"event": "teardown"})

    def _close_subtree(self, node: Node, seen: set[int]) -> None:
        if id(node) in seen:  # visited-set guard, every walk
            return
        seen.add(id(node))
        for child in node.children.values():
            self._close_subtree(child, seen)
        if node.exposed_bus is not None:
            node.exposed_bus.close()
        if isinstance(node.driver, Transport):
            node.driver.close()
            logger.debug("closed %s", node.path,
                         extra={"event": "teardown", "path": node.path})

    def __enter__(self) -> Hal:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):  # bare load(): cleanup best-effort, documented as such
        try:
            self.close()
        except Exception:  # pragma: no cover
            pass


def load(path) -> Hal:
    roots, ids = load_tree(path)
    return Hal(roots, ids)


# -- LLM tool-schema helpers ----------------------------------------------------

def _effect(fn) -> dict:
    """side_effect + idempotency for an op: explicit @shal.op wins, else inferred
    from @idempotent (a read is 'none' and safe to repeat)."""
    meta = getattr(fn, "__shal_op__", None) or {}
    idem = bool(getattr(fn, "__shal_idempotent__", False))
    side = meta.get("side_effect") or ("none" if idem else "write")
    return {"side_effect": side, "idempotent": idem, "unit": meta.get("unit")}


def _describe(node: Node, opname: str, fn) -> str:
    meta = getattr(fn, "__shal_op__", None) or {}
    eff = _effect(fn)
    doc_first = (fn.__doc__ or "").strip().splitlines()[0].strip() if fn.__doc__ else ""
    base = meta.get("description") or doc_first or f"Invoke '{opname}'."
    parts = [base, f"Device '{node.id or node.path}' at {node.path}."]
    if eff["unit"]:
        parts.append(f"Unit: {eff['unit']}.")
    if eff["idempotent"]:
        parts.append("Idempotent read — safe to call repeatedly.")
    else:
        parts.append(f"Side effect ({eff['side_effect']}): a failed call may have "
                     f"partially applied and is NOT auto-retried — confirm before re-calling.")
    return " ".join(parts)


def _params_schema(fn) -> dict:
    """JSON Schema for an op's parameters, from its type hints."""
    sig = inspect.signature(fn)
    try:
        hints = typing.get_type_hints(fn)
    except Exception:  # pragma: no cover - unresolvable annotation
        hints = {}
    props: dict[str, dict] = {}
    required: list[str] = []
    for pname, p in sig.parameters.items():
        if pname == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        props[pname] = _json_type(hints.get(pname))
        if p.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def _json_type(hint) -> dict:
    if hint in _JSON_TYPES:
        return {"type": _JSON_TYPES[hint]}
    # Optional[X] / X | None -> the inner type
    args = [a for a in typing.get_args(hint) if a is not type(None)]
    if len(args) == 1 and args[0] in _JSON_TYPES:
        return {"type": _JSON_TYPES[args[0]]}
    return {"type": "string"}
