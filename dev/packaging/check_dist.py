#!/usr/bin/env python
"""Check the *built wheel's* metadata against what ``src/shal`` actually needs
(shal#123).

CI installs `.[dev]` from a repo checkout, so it never once installs the
thing pip actually ships. That gap is not hypothetical: pyshal 0.2.1 declared
`mcp>=1.0` with no upper bound, and `pip install "pyshal[mcp]"` broke on
every fresh install for 34 days while every test stayed green (#105/#106) —
because no test ever installed the wheel and imported it.

An in-repo test can't fix this: it would re-open pyproject.toml and assert a
substring, which just restates the diff and passes just as happily with a
wrong bound. So this script reads the *built artifact* instead — the wheel's
``Requires-Dist``, from its own METADATA — and checks two things:

1. Every third-party import that runs at module scope in ``src/shal`` (i.e.
   at `import shal` time) is a declared dependency. A **lazy** import — one
   nested inside a function or method body, like the `mcp` SDK import in
   `src/shal/mcp/server.py` or the `jsonschema` import in
   `src/shal/loader.py:_validate_schema` — only breaks the feature that
   calls it, not `import shal`, so it is deliberately NOT required to be
   module-scope-visible here. The axis is module-scope vs. lazy, not
   "imported anywhere".

2. Every declared dependency (base install and extras) has an upper bound,
   or a written, reviewed exemption in `UNBOUNDED_EXEMPT` below. An
   unbounded floor is exactly the shape that caused #105: `mcp>=1.0` let a
   `2.x` in.

Usage:
    check_dist.py DIST_DIR

``DIST_DIR`` must hold exactly one built wheel (the output of
``python -m build``, e.g. ``dist/``). Exit 0 on success, 1 with the specific
failures listed on stderr otherwise.
"""
from __future__ import annotations

import argparse
import ast
import sys
import zipfile
from email.parser import Parser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "shal"
# ADK reference set: guide material never imported by `import shal` (#149)
REFERENCE = SRC / "adk" / "reference"

#: import name -> distribution name, only where they differ (canonicalized)
#: from the import name itself. Add here, with a comment, the next time one
#: does.
IMPORT_TO_DIST = {
    "yaml": "pyyaml",  # `import yaml` ships as the `PyYAML` distribution
}

#: distribution name (canonicalized) -> reason it may ship with no upper
#: bound. Every entry here is a deliberate, reviewed decision, not a place to
#: quiet the check.
#:
#: shal's two runtime deps (pyyaml, jsonschema) and the `mcp` extra all carry
#: real ceilings — those are what #123 is actually about: a break nobody
#: sees because no CI job ever installs what `pip install pyshal` installs.
#: The three `dev`-extra tools below are different in kind, not just degree:
#: nobody's plain `pip install pyshal` pulls them in, and — unlike the
#: runtime deps before this job existed — CI's `test`/`examples` jobs
#: install and run against them on *every* PR already, so a breaking major
#: goes red immediately instead of silently for 34 days like #105/#106.
_DEV_TOOL_REASON = (
    "dev-only tool (never installed by a plain `pip install pyshal`); CI's "
    "`test`/`examples` jobs install and exercise it on every PR, so a "
    "breaking major fails loudly right away instead of silently like #105.")
UNBOUNDED_EXEMPT: dict[str, str] = {
    "pytest": _DEV_TOOL_REASON,
    "pytest-cov": _DEV_TOOL_REASON,
    "ruff": _DEV_TOOL_REASON,
}

#: specifier operators that put a ceiling on the version (`~=` and `==` both
#: exclude some future major, same as an explicit `<`).
_UPPER_BOUND_OPS = {"<", "<=", "==", "~="}


def _module_scope_third_party_imports(path: Path) -> set[str]:
    """Import names reachable when this module is imported — i.e. NOT nested
    inside a function/method body (a `def` guards its body until called; a
    class body runs at import time, so imports there still count)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue  # its body only runs when called — lazy, not module-scope
            if isinstance(child, ast.Import):
                found.update(alias.name.split(".")[0] for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                if child.level == 0 and child.module:  # skip `from . import x`
                    found.add(child.module.split(".")[0])
            visit(child)

    visit(tree)
    return found - set(sys.stdlib_module_names) - {"shal"}


def _collect_required_imports() -> dict[str, list[str]]:
    """import name -> sorted list of files (relative to repo root) that
    import it at module scope."""
    by_import: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if path.is_relative_to(REFERENCE):
            continue
        for name in _module_scope_third_party_imports(path):
            by_import.setdefault(name, []).append(str(path.relative_to(ROOT)))
    return by_import


def _read_wheel_requires(wheel_path: Path) -> list[Requirement]:
    with zipfile.ZipFile(wheel_path) as zf:
        metadata_names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        if len(metadata_names) != 1:
            raise SystemExit(
                f"expected exactly one *.dist-info/METADATA in {wheel_path}, "
                f"found {metadata_names}")
        raw = zf.read(metadata_names[0]).decode("utf-8")
    msg = Parser().parsestr(raw)
    return [Requirement(line) for line in msg.get_all("Requires-Dist") or []]


def _find_wheel(dist_dir: Path) -> Path:
    wheels = sorted(dist_dir.glob("*.whl"))
    if len(wheels) != 1:
        raise SystemExit(
            f"expected exactly one wheel in {dist_dir}, found {[w.name for w in wheels]}")
    return wheels[0]


def check(dist_dir: Path) -> list[str]:
    wheel = _find_wheel(dist_dir)
    reqs = _read_wheel_requires(wheel)

    # Base-install dependency names only (no `; extra == "..."` marker) — an
    # extra-gated dep is not needed for `import shal` to work, so it's out of
    # scope for check 1. It's still in scope for check 2 (bounds).
    base_dist_names = {
        canonicalize_name(r.name) for r in reqs
        if not (r.marker and "extra" in str(r.marker))
    }

    errors: list[str] = []

    # 1. every module-scope third-party import must be a declared dependency.
    for import_name, files in sorted(_collect_required_imports().items()):
        dist_name = canonicalize_name(IMPORT_TO_DIST.get(import_name, import_name))
        if dist_name not in base_dist_names:
            where = files[0] + (f" (+{len(files) - 1} more)" if len(files) > 1 else "")
            errors.append(
                f"module-scope import '{import_name}' in {where} has no matching "
                f"base dependency in the wheel's Requires-Dist (looked for "
                f"'{dist_name}'). Declare it in pyproject.toml [project.dependencies], "
                f"or add an IMPORT_TO_DIST entry in {Path(__file__).name} if the "
                f"distribution name genuinely differs from the import name.")

    # 2. every declared dependency needs an upper bound, or a written exemption.
    for r in reqs:
        dist = canonicalize_name(r.name)
        has_upper = any(spec.operator in _UPPER_BOUND_OPS for spec in r.specifier)
        if not has_upper and dist not in UNBOUNDED_EXEMPT:
            errors.append(
                f"'{r}' has no upper bound and no exemption. Add a ceiling in "
                f"pyproject.toml (e.g. '<{'X'}' for the next major), or add "
                f"UNBOUNDED_EXEMPT['{dist}'] = '<reason>' in {Path(__file__).name}.")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "dist_dir", type=Path, help="directory holding the built *.whl (python -m build output)")
    args = parser.parse_args()

    errors = check(args.dist_dir)
    if errors:
        print("packaging check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    wheel = _find_wheel(args.dist_dir)
    n_imports = len(_collect_required_imports())
    n_reqs = len(_read_wheel_requires(wheel))
    print(f"packaging check ok for {wheel.name}: {n_imports} module-scope third-party "
          f"import(s) all declared, {n_reqs} declared dependenc"
          f"{'y' if n_reqs == 1 else 'ies'} all bounded or exempt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
