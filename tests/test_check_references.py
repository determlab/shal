"""The ADK reference-set checker (#153): listing, class discovery, the verdict.

The checker lives under dev/adk/ (never shipped in the wheel); add it to
sys.path so this test can import it. The real run — a clean venv, the built
wheel, the installed reference folders — is the `references` CI job.
"""
from __future__ import annotations

import sys
from importlib.resources import files
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "adk"))

from check_references import (  # noqa: E402
    BadReference,
    driver_class,
    list_references,
    verdict,
)

CLEAN = {"compatible": "ti,tmp102", "ok": True, "problems": [], "warnings": [],
         "checked": ["static: capability ops discovered"]}


def test_lists_every_folder_with_a_driver_py(tmp_path):
    for name in ("b_ref", "a_ref", "no_sim"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "driver.py").write_text("", encoding="utf-8")
    (tmp_path / "a_ref" / "sim.py").write_text("", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()        # a folder without driver.py is not one
    (tmp_path / "notes").mkdir()
    (tmp_path / "driver.py").write_text("", encoding="utf-8")  # a file, not a folder
    assert [p.name for p in list_references(tmp_path)] == ["a_ref", "b_ref", "no_sim"]


def test_lists_the_shipped_set():
    # names, not a count: more references join the set
    root = Path(str(files("shal") / "adk" / "reference"))
    names = {p.name for p in list_references(root)}
    assert {"tmp102", "mcp23017", "rigol_dp832", "sonos", "order_service", "sqlite",
            "kvstore"} <= names


def test_every_shipped_driver_names_its_class():
    root = Path(str(files("shal") / "adk" / "reference"))
    got = {p.name: driver_class(p / "driver.py") for p in list_references(root)}
    assert got["tmp102"] == "Tmp102" and got["sqlite"] == "SqliteDatabase"
    assert got["kvstore"] == "DbmStore"  # one registered Driver, beside a plain Error class


def test_driver_class_is_the_registered_driver_subclass(tmp_path):
    src = ("from shal import registry\nfrom shal.driver import Driver\n"
           "class Helper:\n    pass\n"
           "class Base(Driver):\n    pass\n"            # a Driver, but not registered
           "@registry.register\nclass Thing(Base, Driver):\n    compatible = 'x,y'\n")
    (tmp_path / "driver.py").write_text(src, encoding="utf-8")
    assert driver_class(tmp_path / "driver.py") == "Thing"


def test_driver_class_refuses_zero_or_two(tmp_path):
    p = tmp_path / "driver.py"
    p.write_text("class Helper:\n    pass\n", encoding="utf-8")
    with pytest.raises(BadReference, match="found 0"):
        driver_class(p)
    p.write_text("import shal\n@shal.registry.register\nclass A(shal.driver.Driver): ...\n"
                 "@register\nclass B(Driver): ...\n", encoding="utf-8")
    with pytest.raises(BadReference, match="found 2"):
        driver_class(p)


def test_verdict_passes_a_clean_report():
    assert verdict(CLEAN) == []


def test_verdict_fails_a_report_with_a_problem():
    report = {**CLEAN, "ok": False, "problems": ["op 'x' has no docstring"]}
    assert verdict(report) == ["problem: op 'x' has no docstring"]


def test_verdict_fails_a_report_with_only_a_warning():
    # `ok` is still true (shal check exits 0) — a warning fails here anyway
    report = {**CLEAN, "warnings": ["op 'set_output' declares no side_effect"]}
    assert verdict(report) == ["warning: op 'set_output' declares no side_effect"]
