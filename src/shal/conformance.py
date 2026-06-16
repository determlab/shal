"""Conformance kit (DESIGN V2: 'a conformance kit lets a new bus / driver /
capability self-certify against the contracts — product, not scaffolding').

The check a GENERATED driver must pass before it counts as done (issue #10):

    report = shal.conformance.check_driver("vendor,part", topology="sim.yaml")
    assert report.ok, report.problems

Static checks need only the registered class; live checks additionally load the
given sim topology, bind the driver, and probe the trust mechanisms for real:
declared limits actually reject (LimitError, pre-I/O by construction), write
ops actually hit the audit channel, declared capabilities actually isinstance.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from . import registry
from .approval import AutoApprove, approver
from .driver import inferred_side_effect
from .errors import LimitError
from .transport import Transport

_NUMERIC = {"number", "integer"}


@dataclass
class Report:
    compatible: str
    problems: list[str] = field(default_factory=list)   # empty == certified
    warnings: list[str] = field(default_factory=list)   # advisory only
    checked: list[str] = field(default_factory=list)    # which checks ran

    @property
    def ok(self) -> bool:
        return not self.problems

    def __str__(self) -> str:  # pragma: no cover - human convenience
        lines = [f"conformance {self.compatible}: "
                 f"{'OK' if self.ok else f'{len(self.problems)} problem(s)'}"]
        lines += [f"  PROBLEM  {p}" for p in self.problems]
        lines += [f"  warning  {w}" for w in self.warnings]
        lines += [f"  checked  {c}" for c in self.checked]
        return "\n".join(lines)


def check_driver(compatible_or_cls: str | type, topology: Any = None) -> Report:
    """Certify a driver against the authoring contract. `topology` (a path to a
    YAML whose tree binds this driver to a SIM bus) enables the live probes —
    the dry-run half of the trust story."""
    if isinstance(compatible_or_cls, str):
        cls = registry.resolve(compatible_or_cls)
        compatible = compatible_or_cls
    else:
        cls = compatible_or_cls
        compatible = getattr(cls, "compatible", cls.__name__)
    report = Report(compatible=compatible)
    _static_checks(cls, report)
    if topology is not None:
        _live_checks(cls, topology, report)
    return report


# -- static: the class alone --------------------------------------------------------

def _static_checks(cls: type, report: Report) -> None:
    ops = cls.capability_ops()
    report.checked.append("static: capability ops discovered")
    if not ops and not issubclass(cls, Transport):
        report.problems.append("driver defines no capability ops")

    if not issubclass(cls, Transport):
        if not getattr(cls, "llm_ready", False):
            report.problems.append(
                "device driver must set llm_ready = True (enforces @shal.op "
                "metadata at bind — the agent surface is the product)")
        for name, fn in ops.items():
            meta = getattr(fn, "__shal_op__", None) or {}
            if not meta.get("description"):
                report.problems.append(f"{name}: missing @shal.op description")

    # catalog entry must build, and every schema in it must be valid JSON Schema
    try:
        import jsonschema
        entry = registry.catalog(getattr(cls, "compatible", "")) \
            if getattr(cls, "compatible", "") else {}
        for key in ("address_schema", "config_schema", "child_address_schema"):
            schema = entry.get(key)
            if schema:
                jsonschema.Draft202012Validator.check_schema(schema)
        for op_entry in entry.get("ops") or []:
            jsonschema.Draft202012Validator.check_schema(op_entry["input_schema"])
        report.checked.append("static: catalog entry + schemas well-formed")
    except Exception as e:  # noqa: BLE001 - report, don't crash the kit
        report.problems.append(f"catalog entry failed to build: {e}")

    # unbounded numeric write params: legal, but worth a human look (issue #10:
    # safe operating limits should be DECLARED wherever they exist)
    from . import limits as _limits
    for name, fn in ops.items():
        meta = getattr(fn, "__shal_op__", None) or {}
        side = inferred_side_effect(fn)
        if side == "none":
            continue
        declared = meta.get("params") or {}
        schema = _limits.merged_params_schema(fn)
        for pname, prop in schema["properties"].items():
            if prop.get("type") in _NUMERIC and pname not in declared:
                report.warnings.append(
                    f"{name}: numeric write param '{pname}' has no declared "
                    f"limit — if the device has a safe operating range, "
                    f"declare it in @shal.op(params=...)")
    report.checked.append("static: limit declarations reviewed")


# -- live: bound to a sim topology ----------------------------------------------------

def _live_checks(cls: type, topology: Any, report: Report) -> None:
    from .hal import load
    with load(topology) as hal:
        node = _find_node(hal, cls)
        if node is None:
            report.problems.append(
                f"topology {topology} binds no node to this driver")
            return
        drv = node.driver
        report.checked.append(f"live: bound at {node.path} on a sim transport (dry-run)")

        _probe_capabilities(drv, report)
        _probe_limits(hal, node, report)
        _probe_audit(hal, node, report)


def _find_node(hal, cls: type):
    for root in hal._roots:
        for node in root.walk():
            if isinstance(node.driver, cls):
                return node
    return None


def _probe_capabilities(drv, report: Report) -> None:
    from . import capabilities as caps
    claimed = []
    for name in dir(caps):
        proto = getattr(caps, name, None)
        if (isinstance(proto, type) and not name.startswith("_")
                and getattr(proto, "_is_runtime_protocol", False)
                and isinstance(drv, proto)):
            claimed.append(name)
    if claimed:
        report.checked.append(f"live: capabilities verified ({', '.join(claimed)})")


def _probe_limits(hal, node, report: Report) -> None:
    """For every declared numeric bound, call past it and require LimitError.
    Enforcement is framework-side BEFORE the op body, so a raise here proves
    the device could not have been reached."""
    schemas = getattr(node.driver, "_op_schemas", {}) or {}
    probed = 0
    for opname, schema in schemas.items():
        for pname, prop in schema["properties"].items():
            for kw, delta in (("maximum", 1), ("minimum", -1)):
                if kw not in prop:
                    continue
                bad = prop[kw] + delta
                others = {p: _sample(s) for p, s in schema["properties"].items()
                          if p != pname and p in (schema.get("required") or [])}
                try:
                    getattr(node.driver, opname)(**{pname: bad}, **others)
                    report.problems.append(
                        f"{opname}: declared {pname} {kw}={prop[kw]} did NOT "
                        f"reject {bad} — enforcement broken")
                except LimitError:
                    probed += 1
                except Exception as e:  # noqa: BLE001
                    report.problems.append(
                        f"{opname}: limit probe raised {type(e).__name__} "
                        f"instead of LimitError: {e}")
    if probed:
        report.checked.append(f"live: limits enforced pre-I/O ({probed} probe(s))")


def _probe_audit(hal, node, report: Report) -> None:
    """A write op must leave an audit record (trust mechanism: audit trail)."""
    schemas = getattr(node.driver, "_op_schemas", {}) or {}
    target = None
    for opname in type(node.driver).capability_ops():
        fn = type(node.driver).capability_ops()[opname]
        side = inferred_side_effect(fn)
        if side != "none":
            target = opname
            break
    if target is None:
        return  # read-only device: nothing to audit

    records: list[logging.LogRecord] = []

    class _Collect(logging.Handler):
        def emit(self, record):  # noqa: D102
            records.append(record)

    audit = logging.getLogger("shal.audit")
    handler = _Collect(level=logging.INFO)
    audit.addHandler(handler)
    prior = audit.level
    audit.setLevel(logging.INFO)
    try:
        schema = schemas.get(target) or {"properties": {}, "required": []}
        kwargs = {p: _sample(s) for p, s in schema["properties"].items()
                  if p in (schema.get("required") or [])}
        try:
            # conformance is the sim/CI definition-of-done: auto-approve so a gated
            # (actuator/config) op actually reaches I/O and audits its real outcome,
            # rather than the default policy denying it headlessly (issue #14).
            with approver(AutoApprove()):
                getattr(node.driver, target)(**kwargs)
        except Exception:  # noqa: BLE001 - outcome irrelevant; the RECORD matters
            pass
        if any(getattr(r, "op", "") == target for r in records):
            report.checked.append(f"live: audit trail present ({target})")
        else:
            report.problems.append(
                f"{target}: write op produced no shal.audit record")
    finally:
        audit.removeHandler(handler)
        audit.setLevel(prior)


def _sample(prop: dict) -> Any:
    """An in-range sample value for a property schema (for probe calls)."""
    if "enum" in prop:
        return prop["enum"][0]
    t = prop.get("type")
    if t in _NUMERIC:
        lo = prop.get("minimum", 0)
        hi = prop.get("maximum", lo)
        value = lo if lo == hi else (lo + hi) / 2
        return int(value) if t == "integer" else float(value)
    if t == "boolean":
        return False
    return "probe"
