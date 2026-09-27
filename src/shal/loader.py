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
from .log import redact_url
from .node import Node
from .transport import Transport

logger = logging.getLogger("shal.loader")

_SCHEMA_PATH = Path(__file__).parent / "schema" / "shal-v1.schema.json"
_ENV_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")
_PARAM_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
_NODE_KEYS = {"id", "description", "expose", "driver", "address", "routes", "to",
              "children", "watchdog_ms", "verify", "insecure", "config", "from",
              "use", "with"}


def _parse_dotenv(text: str) -> dict[str, str]:
    """Minimal `.env` parser (no dependency): `KEY=VALUE` per line, `#` comments,
    optional leading `export`, surrounding single/double quotes stripped. An
    *unquoted* value drops an inline ` # comment` (a `#` after whitespace, or a `#`
    that begins the value) — so `HOST=h.example # prod` resolves to `h.example` and a
    blank-with-comment `HOST=   # set me` resolves to empty/unset, not the comment text
    (#86); a `#` inside quotes or with no leading space stays literal."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not key:
            continue
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]                       # quoted: inner `#` is literal
        elif val.startswith("#"):                 # the whole value is a comment -> unset
            val = ""
        else:
            cut = next((i for i in range(1, len(val))
                        if val[i] == "#" and val[i - 1] in " \t"), None)
            if cut is not None:                   # unquoted: drop inline ` # comment`
                val = val[:cut].rstrip()
        out[key] = val
    return out


def _gitignored(env_path: Path) -> bool:
    """True if `.env` looks ignored anywhere up to the git root (or there is no
    git root — nothing to leak to). Cheap line match, no git invocation."""
    for parent in [env_path.parent, *env_path.parent.parents]:
        gi = parent / ".gitignore"
        if gi.exists():
            lines = {ln.strip().rstrip("/")
                     for ln in gi.read_text(encoding="utf-8", errors="ignore").splitlines()}
            if {".env", "*.env", ".env*"} & lines:
                return True
        if (parent / ".git").exists():
            return False        # reached the repo root without an ignore
    return True                 # not inside a git repo


def _apply_dotenv(base_dir: Path) -> None:
    """Load a `.env` beside the topology into the environment so `${ENV}`
    placeholders resolve — secrets never touch a host config. Real environment
    variables WIN (a deliberately-set value is never clobbered). Agent-agnostic:
    the core does this; no agent host knows about it (#73)."""
    env_path = base_dir / ".env"
    if not env_path.exists():
        return
    pairs = _parse_dotenv(env_path.read_text(encoding="utf-8"))
    applied = 0
    for k, v in pairs.items():
        if k not in os.environ:         # real env wins
            os.environ[k] = v
            applied += 1
    if applied:
        logger.debug("applied %d var(s) from .env", applied,
                     extra={"event": "dotenv", "file": str(env_path)})
    if pairs and not _gitignored(env_path):
        logger.warning(".env loaded but not gitignored — add '.env' to .gitignore "
                       "so secrets aren't committed",
                       extra={"event": "dotenv_unignored", "file": str(env_path)})


def load_tree(
    source: str | os.PathLike | Mapping,
) -> tuple[list[Node], dict[str, Node], dict | None]:
    """Load a topology from a YAML file path, or from an in-memory mapping (the
    shape produced by curated/zero-config entry and a future setup flow). A dict
    is taken as the already-parsed document; includes (`use:`, `include:`) in a
    dict topology resolve from the current working directory.

    `include:` (top-level list of relative paths) composes a setup from many
    files: each included file is a full topology with its own `root:`, and
    those `root:` maps merge as SIBLINGS into the main file's `root:` — nothing
    nests (shal#134). There is no override: a duplicate top-level name across
    any two files is a `LoadError` naming both files, so include order never
    matters. Only the main file's `.env` is read (`_apply_dotenv` runs once,
    above, before any include is even parsed) — an included file's sibling
    `.env` is never consulted, so one setup has exactly one source of secrets.

    The third value is the main file's top-level `policy:` mapping (or None) —
    the operator's approval policy, kept on the Hal `shal.load` returns
    (ADR-001 addendum 5b), never in process state.
    Only the main file may declare it: an included file or a `use:` template
    that carries `policy:` is a `LoadError`."""
    if isinstance(source, Mapping):
        doc: Any = source
        src_label = "<dict>"
        base_dir = Path.cwd()
        seen: tuple[Path, ...] = ()
    else:
        top = Path(source).resolve()
        _apply_dotenv(top.parent)  # `.env` beside the topology -> ${ENV} (#73), real env wins
        doc = yaml.safe_load(top.read_text(encoding="utf-8"))  # safe_load only — invariant
        src_label = str(source)
        base_dir = top.parent
        seen = (top,)
    _validate_schema(doc, source=src_label)

    roots: list[Node] = []
    ids: dict[str, Node] = {}
    refs: list[tuple[Node, str]] = []
    routed: list[tuple[Node, list]] = []
    ctx = _IncludeCtx(top_root=base_dir, base_dir=base_dir, seen=seen)

    merged = _merge_includes(doc, src_label, ctx)
    for name, (spec, node_ctx, _origin) in merged.items():
        roots.append(_build(name, spec, parent=None, ids=ids, refs=refs,
                            routed=routed, ctx=node_ctx))

    # $ref: loader links instead of recursing, so load terminates
    for node, ref in refs:
        target = ids.get(ref[1:])
        if target is None:
            raise LoadError(f"{node.path}: unresolved $ref '{ref}'")
        node.ref_target = target
        logger.debug("linked %s -> %s", ref, target.path,
                     extra={"event": "ref", "path": node.path})

    # routes: `via` resolves against the whole tree, like `to:`, so it may point forward
    if routed:
        by_path = {n.path: n for r in roots for n in r.walk()}
        for node, jumps in routed:
            _resolve_routes(node, jumps, by_path)

    _bind_drivers(roots)
    n_nodes = sum(1 for r in roots for _ in r.walk())
    logger.info("topology loaded: %d nodes, %d ids, %d refs",
                n_nodes, len(ids), len(refs),
                extra={"event": "loaded", "file": src_label})
    return roots, ids, doc.get("policy")


_ADDRESS_ROUTES_TO = ("address", "routes", "to")


def _is_address_routes_to_oneof(error: Any) -> bool:
    """True if `error` is the node-level oneOf that enforces "exactly one of
    address/routes/to" ($defs/node/allOf[0]/then/oneOf, #95). Identified by the
    *shape* of the failing subschemas (three branches, each `required: [<key>]`
    for one of the three keys) rather than by its schema_path, so it still
    matches through $ref/children recursion and survives a schema reformat."""
    if error.validator != "oneOf":
        return False
    branches = error.validator_value
    if not isinstance(branches, list) or len(branches) != len(_ADDRESS_ROUTES_TO):
        return False
    required = set()
    for branch in branches:
        req = branch.get("required") if isinstance(branch, dict) else None
        if not (isinstance(req, list) and len(req) == 1):
            return False
        required.add(req[0])
    return required == set(_ADDRESS_ROUTES_TO)


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
        instance = first.instance
        # #95: a node with NONE of address/routes/to (the common "forgot the
        # address on a cloud/config-only device" slip) trips this same oneOf as
        # a node that has e.g. BOTH address and to — jsonschema's rendering
        # can't tell the two apart ("is not valid under any of the given
        # schemas"). Disambiguate on the instance itself: only a node missing
        # all three (and not a `use:` node, which legitimately has none —
        # it inherits them from its template, and never reaches this oneOf in
        # the first place since the `if: not required use` guard excludes it,
        # checked again here for safety) gets the friendly rewrite. A node that
        # HAS an address but fails for some other reason never matches
        # (`_is_address_routes_to_oneof` requires validator == "oneOf" on THIS
        # error, and any node with a real address that's merely misshapen
        # elsewhere fails a different, non-oneOf validator first) — so it keeps
        # getting its own honest message instead of being misdiagnosed.
        if (_is_address_routes_to_oneof(first) and isinstance(instance, dict)
                and "use" not in instance
                and not any(k in instance for k in _ADDRESS_ROUTES_TO)):
            raise LoadError(f"{source}: node '{where}' needs exactly one of "
                            f"address | routes | to")
        # a routed node keeps its main route in `address:` (under its parent bus)
        if (_is_address_routes_to_oneof(first) and isinstance(instance, dict)
                and "use" not in instance and "routes" in instance
                and "address" not in instance and "to" not in instance):
            raise LoadError(f"{source}: node '{where}' has routes but no address; "
                            f"add address: for its main route, under its parent bus")
        raise LoadError(f"{source}: schema violation at {where}: {first.message}"
                        + (f" (+{len(errors) - 1} more)" if len(errors) > 1 else ""))


class _IncludeCtx:
    """Carries the include search context down the recursion: where relative
    `use:`/`include:` paths resolve from, the top-level dir they may not
    escape, and the chain of files already open (cycle guard). Immutable;
    `descend` forks it."""

    __slots__ = ("top_root", "base_dir", "seen")

    def __init__(self, top_root: Path, base_dir: Path, seen: tuple[Path, ...]) -> None:
        self.top_root = top_root
        self.base_dir = base_dir
        self.seen = seen

    def descend(self, into: Path) -> _IncludeCtx:
        return _IncludeCtx(self.top_root, into.parent, (*self.seen, into))


def _confine(target: Path, top_root: Path, *, what: str) -> None:
    """Shared by `use:` and `include:`: neither may escape the main topology's
    directory tree — same rule, same reason (a path that climbs out reads
    arbitrary files). Reusing a board from another project means copying it
    in, not path-climbing to it."""
    try:
        target.relative_to(top_root)
    except ValueError as e:
        raise LoadError(f"{what} escapes the topology root {top_root} "
                        f"(includes must stay within the project tree)") from e


def _merge_includes(
    doc: Mapping, file_label: str, ctx: _IncludeCtx,
) -> dict[str, tuple[Any, _IncludeCtx, str]]:
    """Resolve `include:` into one merged `root:` mapping: sibling files' `root:`
    maps merge as siblings into this file's `root:` — nothing nests (shal#134).
    Each value carries the `_IncludeCtx` a *that* entry's node tree must build
    with (so a relative `use:` inside an included file resolves against the
    included file's own directory, not the main file's) and the file that
    defined it (for the duplicate-name error, which must name both files).

    No override: a name already merged from another file is a `LoadError`
    naming both files — so include order never matters. Confinement and the
    cycle guard reuse the same `_IncludeCtx` machinery `use:` uses (`.seen`,
    `top_root`, `.descend`); schema validation runs on every included file,
    before it is merged, so a violation names that file."""
    merged: dict[str, tuple[Any, _IncludeCtx, str]] = {}
    for name, spec in (doc.get("root") or {}).items():
        merged[name] = (spec, ctx, file_label)

    for rel in doc.get("include") or []:
        target = (ctx.base_dir / rel).resolve()
        _confine(target, ctx.top_root, what=f"include '{rel}' (from {file_label}):")
        if target in ctx.seen:  # cycle guard, same set `use:` extends
            chain = " -> ".join(p.name for p in (*ctx.seen, target))
            raise LoadError(f"circular include: {chain}")
        if not target.is_file():
            raise LoadError(f"include '{rel}' (from {file_label}): file not found: {target}")

        sub_doc = yaml.safe_load(target.read_text(encoding="utf-8"))  # safe_load only
        _validate_schema(sub_doc, source=str(target))  # per file, before merge
        _no_policy(sub_doc, target)
        # An included file must contribute something: either its own `root:`
        # or a further `include:` chain (legal, shal#136/D21 point 6). A file
        # with neither — e.g. a `use:` template (`template:`, no `root:`) —
        # passes schema (anyOf root/template/include) but reaches nothing when
        # include:d, so the load would otherwise "succeed" while silently
        # loading nothing (shal#137, D21).
        if not (sub_doc.get("root") or sub_doc.get("include")):
            raise LoadError(
                f"include '{rel}' (from {file_label}): {target} is a use: "
                f"template — a use: template cannot be include:d "
                f"(reference it from a node)")
        sub_ctx = ctx.descend(target)
        sub_merged = _merge_includes(sub_doc, str(target), sub_ctx)

        for name, entry in sub_merged.items():
            if name in merged:
                raise LoadError(
                    f"duplicate top-level name '{name}': defined in both "
                    f"{merged[name][2]} and {entry[2]}")
            merged[name] = entry
    return merged


def _no_policy(doc: Mapping, target: Path) -> None:
    """`policy:` is the operator's declaration and lives in the main topology file
    only (ADR-001 addendum 5): a composed file must not carry it."""
    if "policy" in doc:
        raise LoadError(f"{target}: `policy:` may only be set in the main topology "
                        f"file, not in an included file or a use: template")


def _build(name: str, spec: Mapping, *, parent: Node | None,
           ids: dict[str, Node], refs: list, routed: list, ctx: _IncludeCtx) -> Node:
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
        if parent is None:  # the main route is the node's place in the tree
            raise _no_main_bus(node)
        routed.append((node, list(spec["routes"])))
    if "to" in spec:
        refs.append((node, spec["to"]))

    node.spec = dict(spec)  # schema-validated keys only
    if "config" in node.spec:  # ${ENV_VAR} values resolve here; literals pass through
        node.spec["config"] = {k: _resolve_env(v)
                               for k, v in node.spec["config"].items()}
    for cname, cspec in (spec.get("children") or {}).items():
        node.children[cname] = _build(cname, cspec, parent=node, ids=ids,
                                      refs=refs, routed=routed, ctx=ctx)
    return node


def _no_main_bus(node: Node) -> LoadError:
    return LoadError(f"{node.path}: a node with routes must sit under its main bus; "
                     f"put it there and list the other channels as routes")


def _resolve_routes(node: Node, jumps: list, by_path: Mapping[str, Node]) -> None:
    """Store `[main, jump1, ...]` as (name, bus node, address) on the node. The
    main route is the node's place in the tree: its parent, named after it. A
    jump's name defaults to the last segment of its `via`. Until failover lands
    the node binds on its main route only; `_check_jumps` checks the rest."""
    assert node.parent is not None  # _build refused a routed root
    routes: list[tuple[str, Node, Any]] = [(node.parent.name, node.parent, node.address)]
    for r in jumps:
        via = r["via"]
        name = r.get("name") or via.rsplit("/", 1)[-1]
        for other, bus, _ in routes:
            if other == name:
                raise LoadError(
                    f"{node.path}: route {name} via {bus.path} and route {name} "
                    f"via {via} share a name; set name: on one of them")
        target = by_path.get(via)
        if target is None:
            raise LoadError(f"{node.path}: route {name}: no bus at {via}")
        routes.append((name, target, _resolve_env(r["address"])))
    node.routes = routes


def _expand_use(name: str, spec: Mapping, ctx: _IncludeCtx) -> tuple[dict, _IncludeCtx]:
    """Load the `use:` template, substitute `with:` params, and merge: the
    template is the base, any other keys on the using node override it."""
    rel = spec["use"]
    target = (ctx.base_dir / rel).resolve()
    # confinement: no absolute escapes, no climbing above the top-level file's dir
    _confine(target, ctx.top_root, what=f"node '{name}': use '{rel}'")
    if target in ctx.seen:  # cycle guard, every include chain
        chain = " -> ".join(p.name for p in (*ctx.seen, target))
        raise LoadError(f"node '{name}': circular use include: {chain}")
    if not target.is_file():
        raise LoadError(f"node '{name}': use template not found: {target}")

    doc = yaml.safe_load(target.read_text(encoding="utf-8"))  # safe_load only
    if not isinstance(doc, Mapping) or "template" not in doc:
        raise LoadError(f"{target}: a `use:` target must define a top-level "
                        f"`template:` node")
    # a template is reused across the tree, so a jump's bus comes from `with:`
    for rname, via in _template_vias(doc["template"]):
        if not via.startswith("${"):
            raise LoadError(
                f"node '{name}': use '{rel}': route {rname} via {via} is a literal "
                f"path; a via inside a template must be a parameter "
                f"(e.g. ${{rack}}/console2, set from with:)")
    # substitute `with:` params first, THEN validate — so ${param} placeholders
    # (which would violate strict id/address grammars) are already resolved.
    doc = {**doc, "template": _apply_params(doc["template"],
                                            dict(spec.get("with") or {}), target, name)}
    _validate_schema(doc, source=str(target))
    _no_policy(doc, target)
    base = doc["template"]
    merged = {**base, **{k: v for k, v in spec.items() if k not in ("use", "with")}}
    return merged, ctx.descend(target)


def _template_vias(spec: Any):
    """(route name, via) for every jump in a raw template subtree, before `with:`."""
    if not isinstance(spec, Mapping):
        return
    for r in spec.get("routes") or []:
        if isinstance(r, Mapping) and isinstance(r.get("via"), str):
            yield r.get("name") or r["via"].rsplit("/", 1)[-1], r["via"]
    children = spec.get("children")
    for child in (children.values() if isinstance(children, Mapping) else ()):
        yield from _template_vias(child)


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
    """Resolve compatibles, instantiate, validate kinds + address grammar, bind.

    If ANY step raises (#220), what was already bound — each bound Transport
    driver and each child bus one provided — is closed, in reverse order, once,
    and the ORIGINAL exception is re-raised unchanged. A close that raises during
    that cleanup is logged and never replaces it. No Hal exists yet, so the Hal
    fill's cleanup (#217) never runs on top of this one."""
    opened: list[tuple[Node, Transport]] = []
    try:
        _bind_all(roots, opened)
        _check_jumps(roots)  # after every bus is bound: a jump may point forward
    except BaseException:
        for node, t in reversed(opened):
            try:
                t.close()
            except Exception as e:  # noqa: BLE001 — never mask the original
                # the type only: a close message may carry an address (rule 7)
                logger.warning("close after a failed bind raised %s at %s",
                               type(e).__name__, node.path,
                               extra={"event": "bind_cleanup_failed",
                                      "path": node.path})
        raise


def _bind_all(roots: list[Node], opened: list[tuple[Node, Transport]]) -> None:
    for root in roots:
        for node in root.walk():
            compatible = node.spec.get("driver")
            if compatible is None:
                continue  # channel nodes: address only, no driver
            cls = registry.resolve(compatible, prefer_dist=node.spec.get("from"))
            drv = cls(node) if issubclass(cls, Transport) else cls()
            node.driver = drv

            bus = node.parent_bus
            if node.routes and bus is None:  # the parent is not a bus
                raise _no_main_bus(node)
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
            if isinstance(drv, Transport):
                opened.append((node, drv))
            # redact_url: a ${ENV} address may carry userinfo creds (issue #117)
            logger.debug("bound %s at %s", compatible, node.path,
                         extra={"event": "bind", "path": node.path,
                                "addr": redact_url(str(node.address))})
            for child in node.children.values():
                child_bus = drv.provide_child_bus(child)
                if child_bus is not None:
                    child.exposed_bus = child_bus
                    opened.append((child, child_bus))


def _check_jumps(roots: list[Node]) -> None:
    """Each jump's `via` must be a bus that offers the driver's transport kind
    (`kinds()`, never hasattr). The main route was checked at bind."""
    for root in roots:
        for node in root.walk():
            if node.driver is None:
                continue
            need = getattr(type(node.driver), "kind", None)
            for name, target, _addr in node.routes[1:]:
                bus = target.bus
                if bus is None:
                    raise LoadError(f"{node.path}: route {name}: no bus at {target.path}")
                if need is not None and need not in bus.kinds():
                    raise LoadError(
                        f"{node.path}: route {name} via {target.path} offers "
                        f"{', '.join(sorted(k.__name__ for k in bus.kinds()))}, driver "
                        f"{node.spec.get('driver')} needs {need.__name__}")
