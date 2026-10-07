"""Where a player's ``driver.py`` came from (issue #487): written by the agent,
or copied from one of the packaged reference drivers
(``shal_arena/reference_drivers/``, issue #407 / PR #469).

`check`, `measure` and `call` each get a driver file; this compares it with
the packaged reference driver for that instrument's case and labels it
``"reference-copy"`` or ``"agent"``. The comparison is normalized text
similarity (`difflib.SequenceMatcher`, standard library): comments,
docstrings and blank lines are dropped, whitespace is collapsed, and every
name the file itself defines (classes, functions, arguments, variables) is
replaced with a placeholder in order of first use -- so renaming a copy does
not hide it.

Reads only the agent's file and the packaged reference. Nothing is sent
anywhere, nothing is executed, and no side effect changes.
"""
from __future__ import annotations

import ast
import difflib
import re
from pathlib import Path
from typing import Any

#: At or above this similarity (0..1, on the normalized text) a driver is a
#: reference copy. 0.90, not 1.0: a copy with a changed op description, an
#: extra blank helper or a reordered decorator still lands well above it,
#: while a driver written independently for the same datasheet -- its own
#: helpers, parsing and error handling -- lands below it (see
#: arena/tests/test_driver_origin.py for both sides).
REFERENCE_COPY_THRESHOLD = 0.90

REFERENCE_COPY = "reference-copy"
AGENT = "agent"

_REFERENCE_DRIVERS_DIR = Path(__file__).resolve().parent / "reference_drivers"

#: One packaged reference driver per case (same files `ui.play` plays with).
REFERENCE_DRIVERS: dict[str, Path] = {
    "scpi-psu": _REFERENCE_DRIVERS_DIR / "psu_driver.py",
    "dmm": _REFERENCE_DRIVERS_DIR / "dmm_driver.py",
    "relay-modbus": _REFERENCE_DRIVERS_DIR / "relay_driver.py",
    "sht31": _REFERENCE_DRIVERS_DIR / "temp_driver.py",
}


class _StripDocstrings(ast.NodeTransformer):
    def _strip(self, node: Any) -> Any:
        self.generic_visit(node)
        body = node.body
        if (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
        return node

    visit_Module = visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _strip


def _defined_names(tree: ast.AST) -> list[str]:
    """Every name the file itself defines, in a fixed walk order."""
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.arg):
            names.append(node.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.append(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            names.append(node.attr)
    return list(dict.fromkeys(n for n in names if n != "self"))


class _Rename(ast.NodeTransformer):
    def __init__(self, mapping: dict[str, str]) -> None:
        self.mapping = mapping

    def _name(self, name: str) -> str:
        return self.mapping.get(name, name)

    def visit_Name(self, node: ast.Name) -> ast.Name:
        node.id = self._name(node.id)
        return node

    def visit_arg(self, node: ast.arg) -> ast.arg:
        node.arg = self._name(node.arg)
        return node

    def visit_Attribute(self, node: ast.Attribute) -> ast.Attribute:
        self.generic_visit(node)
        node.attr = self._name(node.attr)
        return node

    def _def(self, node: Any) -> Any:
        self.generic_visit(node)
        node.name = self._name(node.name)
        return node

    visit_ClassDef = visit_FunctionDef = visit_AsyncFunctionDef = _def


def normalize(source: str) -> str:
    """The text the comparison runs on: no comments, docstrings or blank
    lines, whitespace collapsed, the file's own names as placeholders."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        # not Python we can parse: fall back to plain text clean-up, no renaming
        lines = (re.sub(r"#.*", "", line).strip() for line in source.splitlines())
        return re.sub(r"\s+", " ", " ".join(line for line in lines if line)).strip()
    tree = _StripDocstrings().visit(tree)
    mapping = {name: f"_n{i}" for i, name in enumerate(_defined_names(tree))}
    tree = _Rename(mapping).visit(tree)
    return re.sub(r"\s+", " ", ast.unparse(tree)).strip()


def similarity(source: str, reference: str) -> float:
    return difflib.SequenceMatcher(None, normalize(source), normalize(reference),
                                   autojunk=False).ratio()


def _verdict(score: float) -> dict[str, Any]:
    return {"driver_origin": REFERENCE_COPY if score >= REFERENCE_COPY_THRESHOLD else AGENT,
            "similarity": round(score, 2)}


def driver_origin(driver_path: str | Path, case_name: str) -> dict[str, Any]:
    """``{"driver_origin": "reference-copy"|"agent", "similarity": <0..1, 2dp>}``
    for ``driver_path`` against the packaged reference driver of
    ``case_name``. A case with no packaged reference is ``"agent"``."""
    reference = REFERENCE_DRIVERS.get(case_name)
    if reference is None:
        return {"driver_origin": AGENT, "similarity": 0.0}
    source = Path(driver_path).read_text(encoding="utf-8")
    return _verdict(similarity(source, reference.read_text(encoding="utf-8")))


def driver_origin_any_case(source: str) -> dict[str, Any]:
    """The same verdict against the closest packaged reference driver, for a
    driver with no instrument attached (`shal-arena ui --driver NAME=PATH`)."""
    best = max((similarity(source, ref.read_text(encoding="utf-8"))
                for ref in REFERENCE_DRIVERS.values()), default=0.0)
    return _verdict(best)
