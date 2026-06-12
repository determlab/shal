"""Operating limits (issue #10): one schema, two trust layers.

A driver declares per-parameter constraints as JSON-Schema fragments on
``@shal.op(params=...)``. The SAME merged schema is (a) advertised verbatim in
``hal.tool_schemas()`` so a model self-polices before calling, and (b) compiled
once at bind and enforced by the framework wrapper BEFORE any bus I/O — a
generated driver cannot forget, weaken, or mis-order the check.

Merge model (narrow-only, validated loudly at bind):
    type-hint skeleton  ⊕  @op params= fragments        (class truth, catalog)
                        ⊕  driver.op_limits()            (instance narrowing)
                        ⊕  node config['limits'][op]     (installation policy)
Each later layer may only TIGHTEN numeric bounds; widening is a LoadError at
load — YAML can never make a rig more dangerous than the datasheet.
"""
from __future__ import annotations

import inspect
import typing
from collections.abc import Callable

from .errors import LimitError, LoadError

_JSON_TYPES = {int: "integer", float: "number", str: "string", bool: "boolean"}

# constraint keywords a fragment may use (kept to the widely-supported subset so
# the advertised input_schema stays equal to the enforced one on any harness)
_ALLOWED_KEYWORDS = frozenset({
    "type", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "enum", "const", "multipleOf", "description", "examples",
})
# the numeric bounds the tighten-only rule applies to: name -> direction
# (+1 = a LARGER value is tighter, -1 = a SMALLER value is tighter)
_BOUNDS = {"minimum": +1, "exclusiveMinimum": +1,
           "maximum": -1, "exclusiveMaximum": -1}


def json_type(hint) -> dict:
    if hint in _JSON_TYPES:
        return {"type": _JSON_TYPES[hint]}
    args = [a for a in typing.get_args(hint) if a is not type(None)]
    if len(args) == 1 and args[0] in _JSON_TYPES:
        return {"type": _JSON_TYPES[args[0]]}
    return {"type": "string"}


def params_skeleton(fn: Callable) -> dict:
    """JSON Schema for an op's parameters, from its type hints (structural truth:
    which params exist, required-ness, base type)."""
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
        props[pname] = json_type(hints.get(pname))
        if p.default is inspect.Parameter.empty:
            required.append(pname)
    schema: dict = {"type": "object", "properties": props, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def validate_fragment(where: str, pname: str, frag: dict) -> None:
    """A params= fragment must be a dict of allowed constraint keywords."""
    if not isinstance(frag, dict):
        raise LoadError(f"{where}: params['{pname}'] must be a JSON-Schema "
                        f"fragment dict, got {type(frag).__name__}")
    unknown = set(frag) - _ALLOWED_KEYWORDS
    if unknown:
        raise LoadError(f"{where}: params['{pname}'] uses unsupported schema "
                        f"keywords {sorted(unknown)} (allowed: "
                        f"{sorted(_ALLOWED_KEYWORDS)})")


def merge_fragment(where: str, prop: dict, pname: str, frag: dict,
                   *, narrow_only: bool = False) -> dict:
    """Merge one parameter fragment over its current property schema.
    `narrow_only` enforces the tighten-only rule (instance/installation layers)."""
    validate_fragment(where, pname, frag)
    if "type" in frag and "type" in prop and frag["type"] != prop["type"]:
        raise LoadError(f"{where}: params['{pname}'] type '{frag['type']}' "
                        f"contradicts the type hint ('{prop['type']}')")
    out = dict(prop)
    for key, value in frag.items():
        if narrow_only and key in _BOUNDS and key in out:
            tighter_if_larger = _BOUNDS[key] > 0
            widens = value < out[key] if tighter_if_larger else value > out[key]
            if widens:
                raise LoadError(
                    f"{where}: {pname}.{key}={value} would WIDEN the declared "
                    f"limit {out[key]} — limits may only tighten")
        out[key] = value
    if ("minimum" in out and "maximum" in out and out["minimum"] > out["maximum"]):
        raise LoadError(f"{where}: {pname} limit is empty "
                        f"(minimum {out['minimum']} > maximum {out['maximum']})")
    return out


def merged_params_schema(fn: Callable) -> dict:
    """Class-level merged schema: hint skeleton ⊕ @op params= fragments.
    This is what catalog() shows and what tool_schemas() starts from."""
    schema = params_skeleton(fn)
    meta = getattr(fn, "__shal_op__", None) or {}
    fragments = meta.get("params") or {}
    where = f"@op on {getattr(fn, '__qualname__', fn.__name__)}"
    for pname, frag in fragments.items():
        if pname not in schema["properties"]:
            raise LoadError(f"{where}: params['{pname}'] does not name a "
                            f"parameter of the op (has: "
                            f"{sorted(schema['properties'])})")
        schema["properties"][pname] = merge_fragment(
            where, schema["properties"][pname], pname, frag)
    return schema


class Guard:
    """Compiled, bind-time limit gate for one op. check() runs BEFORE the op
    body — i.e. provably before any bus I/O."""

    def __init__(self, fn: Callable, schema: dict, *, path: str, opname: str) -> None:
        import jsonschema  # core dep; lazy so `import shal` stays light
        jsonschema.Draft202012Validator.check_schema(schema)
        self._sig = inspect.signature(fn)
        self._schema = schema
        self._validator = jsonschema.Draft202012Validator(schema)
        self._path = path
        self._op = opname

    @property
    def schema(self) -> dict:
        return self._schema

    def check(self, instance, *args, **kwargs) -> None:
        bound = self._sig.bind(instance, *args, **kwargs)
        bound.apply_defaults()
        payload = {k: v for k, v in bound.arguments.items() if k != "self"}
        violations = []
        for err in self._validator.iter_errors(payload):
            pname = err.path[0] if err.path else None
            limit_kw = err.validator if err.validator in _ALLOWED_KEYWORDS else None
            violations.append({"param": pname, "value": err.instance,
                               "keyword": limit_kw, "limit": err.validator_value,
                               "message": err.message})
        if violations:
            detail = "; ".join(v["message"] for v in violations)
            raise LimitError(
                f"{self._path}  {self._op} rejected by declared limits "
                f"({detail}) — nothing was sent to the device",
                path=self._path, op=self._op, violations=violations)


def effective_schema(driver, fn: Callable, opname: str) -> tuple[dict, bool]:
    """The schema actually enforced (and advertised) for one BOUND op:

        class merged schema  ⊕ driver.op_limits()[op]   (instance, narrow-only)
                             ⊕ node config.limits[op]   (installation, narrow-only)

    Returns (schema, constrained). Raises LoadError — at bind, i.e. during
    shal.load() — on unknown params or any attempt to WIDEN a declared limit."""
    schema = merged_params_schema(fn)
    constrained = bool((getattr(fn, "__shal_op__", None) or {}).get("params"))
    node = getattr(driver, "node", None)
    layers = (
        (f"{type(driver).__name__}.op_limits()",
         (driver.op_limits() or {}).get(opname) or {}),
        (f"{getattr(node, 'path', '?')}: config.limits",
         config_limits(node).get(opname) or {}),
    )
    for where, frags in layers:
        for pname, frag in frags.items():
            if pname not in schema["properties"]:
                raise LoadError(f"{where}: '{pname}' is not a parameter of "
                                f"op '{opname}' (has: "
                                f"{sorted(schema['properties'])})")
            schema["properties"][pname] = merge_fragment(
                where, schema["properties"][pname], pname, frag, narrow_only=True)
            constrained = True
    return schema, constrained


def config_limits(node) -> dict:
    """The node's installation-policy limits: config: {limits: {op: {param: frag}}}.
    The framework owns this key; drivers never see it in their own config parsing."""
    spec = getattr(node, "spec", None) or {}
    cfg = spec.get("config") or {}
    return cfg.get("limits") or {}
