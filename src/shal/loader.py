"""Topology loader.

Invariants (DESIGN V2): yaml.safe_load ONLY; JSON Schema validation first;
id uniqueness; address grammar via the parent bus family; unknown compatible
fails the load; $ref links instead of recursing; secrets resolve from env and
the resolved value never appears in messages or logs.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from . import registry
from .errors import LoadError
from .node import Node
from .transport import Transport

logger = logging.getLogger("shal.loader")

_SCHEMA_PATH = Path(__file__).parent / "schema" / "shal-v1.schema.json"
_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NODE_KEYS = {"id", "description", "expose", "driver", "address", "routes", "to",
              "children", "watchdog_ms", "verify", "insecure", "config", "from",
              "use", "with"}


def load_tree(source: str | os.PathLike | Mapping) -> tuple[list[Node], dict[str, Node]]:
    """Load a topology from a YAML file path, or from an in-memory mapping (the
    shape produced by curated/zero-config entry and a future setup flow). A dict
    is taken as the already-parsed document; includes (`use:`) in a dict topology
    resolve from the current working directory."""
    if isinstance(source, Mapping):
        doc: Any = source
        src_label = "<dict>"
        base_dir = Path.cwd()
        seen: tuple[Path, ...] = ()
    else:
        top = Path(source).resolve()
        doc = yaml.safe_load(top.read_text(encoding="utf-8"))  # safe_load only — invariant
        src_label = str(source)
        base_dir = top.parent
        seen = (top,)
    _validate_schema(doc, source=src_label)

    roots: list[Node] = []
    ids: dict[str, Node] = {}
    refs: list[tuple[Node, str]] = []
    ctx = _IncludeCtx(top_root=base_dir, base_dir=base_dir, seen=seen)

    for name, spec in doc["root"].items():
        roots.append(_build(name, spec, parent=None, ids=ids, refs=refs, ctx=ctx))

    # $ref: loader links instead of recursing, so load terminates
    for node, ref in refs:
        target = ids.get(ref[1:])
        if target is None:
            raise LoadError(f"{node.path}: unresolved $ref '{ref}'")
        node.ref_target = target
        logger.debug("linked %s -> %s", ref, target.path,
                     extra={"event": "ref", "path": node.path})

    _bind_drivers(roots)
    n_nodes = sum(1 for r in roots for _ in r.walk())
    logger.info("topology loaded: %d nodes, %d ids, %d refs",
                n_nodes, len(ids), len(refs),
                extra={"event": "loaded", "file": src_label})
    return roots, ids


def _validate_schema(doc: Any, *, source: str) -> None:
    try:
        import jsonschema
    except ImportError as e:  # declared dependency; message > traceback
        raise LoadError("jsonschema is required to load topologies "
                        "(pip install jsonschema)") from e
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        where = "/".join(str(p) for p in first.absolute_path) or "<root>"
        raise LoadError(f"{source}: schema violation at {where}: {first.message}"
                        + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))


class _IncludeCtx:
    """Carries the include search context down the recursion: where relative
    `use:` paths resolve from, the top-level dir they may not escape, and the
    chain of files already open (cycle guard). Immutable; `descend` forks it."""

    __slots__ = ("top_root", "base_dir", "seen")

    def __init__(self, top_root: Path, base_dir: Path, seen: tuple[Path, ...]) -> None:
        self.top_root = top_root
        self.base_dir = base_dir
        self.seen = seen

    def descend(self, into: Path) -> _IncludeCtx:
        return _IncludeCtx(self.top_root, into.parent, (*self.seen, into))


def _build(name: str, spec: Mapping, *, parent: Node | None,
           ids: dict[str, Node], refs: list, ctx: _IncludeCtx) -> Node:
    # `use:` splices an external template subtree in place of this node's body
    # (device-tree /include/). Happens BEFORE env resolution so `${param}` from
    # `with:` is consumed first; leftover ${VAR} still resolve from the environment.
    # Loop: a template may itself be a use-node (include chains), each step
    # extending the cycle-guard set in ctx.
    while "use" in spec:
        spec, ctx = _expand_use(name, spec, ctx)

    address = _resolve_env(spec.get("address"))
    node = Node(name, address=address, id=spec.get("id"), parent=parent)

    if node.id is not None:
        if node.id in ids:  # globally unique — fail loudly on dupes
            raise LoadError(f"duplicate id '{node.id}': {ids[node.id].path} and {node.path}")
        ids[node.id] = node

    if "routes" in spec:
        raise LoadError(f"{node.path}: routes/failover not implemented in this "
                        f"version (DECISIONS v2.1 #6)")
    if "to" in spec:
        refs.append((node, spec["to"]))

    node.spec = dict(spec)  # schema-validated keys only
    if "config" in node.spec:  # ${ENV_VAR} values resolve here; literals pass through
        node.spec["config"] = {k: _resolve_env(v)
                               for k, v in node.spec["config"].items()}
    for cname, cspec in (spec.get("children") or {}).items():
        node.children[cname] = _build(cname, cspec, parent=node,
                                      ids=ids, refs=refs, ctx=ctx)
    return node


def _expand_use(name: str, spec: Mapping, ctx: _IncludeCtx) -> tuple[dict, _IncludeCtx]:
    """Load the `use:` template, substitute `with:` params, and merge: the
    template is the base, any other keys on the using node override it."""
    rel = spec["use"]
    target = (ctx.base_dir / rel).resolve()
    # confinement: no absolute escapes, no climbing above the top-level file's dir
    try:
        target.relative_to(ctx.top_root)
    except ValueError as e:
        raise LoadError(f"node '{name}': use '{rel}' escapes the topology root "
                        f"{ctx.top_root} (includes must stay within the project tree)") from e
    if target in ctx.seen:  # cycle guard, every include chain
        chain = " -> ".join(p.name for p in (*ctx.seen, target))
        raise LoadError(f"node '{name}': circular use include: {chain}")
    if not target.is_file():
        raise LoadError(f"node '{name}': use template not found: {target}")

    doc = yaml.safe_load(target.read_text(encoding="utf-8"))  # safe_load only
    if not isinstance(doc, Mapping) or "template" not in doc:
        raise LoadError(f"{target}: a `use:` target must define a top-level "
                        f"`template:` node")
    # substitute `with:` params first, THEN validate — so ${param} placeholders
    # (which would violate strict id/address grammars) are already resolved.
    doc = {**doc, "template": _apply_params(doc["template"],
                                            dict(spec.get("with") or {}), target, name)}
    _validate_schema(doc, source=str(target))
    base = doc["template"]
    merged = {**base, **{k: v for k, v in spec.items() if k not in ("use", "with")}}
    return merged, ctx.descend(target)


def _apply_params(value: Any, params: Mapping[str, Any], src: Path, name: str) -> Any:
    """Recursively substitute `${param}` from `with:` into the template. Names
    not in `params` are left untouched (they resolve from the environment later)."""
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            return str(params[m.group(1)]) if m.group(1) in params else m.group(0)
        return _PARAM_RE.sub(repl, value)
    if isinstance(value, Mapping):
        return {k: _apply_params(v, params, src, name) for k, v in value.items()}
    if isinstance(value, list):
        return [_apply_params(v, params, src, name) for v in value]
    return value


def _resolve_env(value: Any) -> Any:
    """${ENV_VAR} -> resolved; missing var names the NAME, never a value."""
    if isinstance(value, str):
        m = _ENV_RE.match(value)
        if m:
            var = m.group(1)
            resolved = os.environ.get(var)
            if resolved is None:
                raise LoadError(f"environment variable '{var}' is not set "
                                f"(referenced as ${{{var}}})")
            # the NAME, never the value (rule 7)
            logger.debug("resolved ${%s} from environment", var,
                         extra={"event": "env"})
            return resolved
    return value


def _bind_drivers(roots: list[Node]) -> None:
    """Resolve compatibles, instantiate, validate kinds + address grammar, bind."""
    for root in roots:
        for node in root.walk():
            compatible = node.spec.get("driver")
            if compatible is None:
                continue  # channel nodes: address only, no driver
            cls = registry.resolve(compatible, prefer_dist=node.spec.get("from"))
            drv = cls(node) if issubclass(cls, Transport) else cls()
            node.driver = drv

            bus = node.parent_bus
            need = getattr(cls, "kind", None)
            if need is not None:
                if bus is None:
                    raise LoadError(f"{node.path}: driver '{compatible}' needs "
                                    f"{need.__name__} but node has no parent bus")
                if need not in bus.kinds():  # kinds(), never hasattr
                    raise LoadError(
                        f"{node.path}: parent bus provides "
                        f"{[k.__name__ for k in bus.kinds()]}, driver needs {need.__name__}")
                bus.validate_address(node.address)  # grammar at load, decision 2

            drv.bind(node)
            logger.debug("bound %s at %s", compatible, node.path,
                         extra={"event": "bind", "path": node.path,
                                "addr": str(node.address)})
            for child in node.children.values():
                child_bus = drv.provide_child_bus(child)
                if child_bus is not None:
                    child.exposed_bus = child_bus
