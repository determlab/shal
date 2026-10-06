"""shal#380: the evidence page, built from the evidence.json files of a
clean-machine run (dev/e2e/story.py, .github/workflows/e2e-clean-machine.yml).

No hand-written schema (#380's Constraints): every cell here is a copy of the
real evidence.json committed at tests/data/e2e/sample-evidence.json, with
os/python/checks edited by this file to produce the pass/fail/missing cases.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_EVIDENCE = REPO_ROOT / "tests" / "data" / "e2e" / "sample-evidence.json"
SCRIPT = REPO_ROOT / "dev" / "e2e" / "evidence_page.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evidence_page", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["evidence_page"] = mod
    spec.loader.exec_module(mod)
    return mod


evidence_page = _load_module()


def _sample() -> dict:
    return json.loads(SAMPLE_EVIDENCE.read_text(encoding="utf-8"))


def _write_cell(root: Path, name: str, doc: dict | None) -> None:
    cell_dir = root / name
    cell_dir.mkdir(parents=True)
    if doc is not None:
        (cell_dir / "evidence.json").write_text(json.dumps(doc), encoding="utf-8")


@pytest.fixture
def matrix_dir(tmp_path: Path) -> Path:
    """A 3-cell matrix built from the real sample, never a hand-written one:
    one passing cell (the sample as-is), one with a failed check, and one
    cell whose evidence.json is missing entirely."""
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()

    passing = _sample()
    _write_cell(ev_dir, "evidence-windows-latest-3.13", passing)

    failing = _sample()
    failing["os"] = "ubuntu-latest"
    failing["python"] = "3.10"
    failing["checks"][0]["result"] = "fail"
    failing["checks"][0]["log"] = "rerun: cd examples && python run_bench.py\nboom"
    _write_cell(ev_dir, "evidence-ubuntu-latest-3.10", failing)

    _write_cell(ev_dir, "evidence-macos-latest-3.10", None)  # no evidence.json at all

    return ev_dir


def test_page_has_repo_shas_os_python_checks_and_log_reference(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    assert evidence_page.main([str(matrix_dir), "--out", str(out)]) == 1
    page = out.read_text(encoding="utf-8")

    sample = _sample()
    for info in sample["versions"].values():
        assert info["repo"] in page
        assert info["sha"] in page

    assert "windows-latest" in page and "3.13" in page
    assert "ubuntu-latest" in page and "3.10" in page

    for check in sample["checks"]:
        assert check["id"] in page

    assert 'href="#log-' in page  # a link to each check's own log


def test_failed_check_renders_fail_and_missing_cell_renders_missing(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    evidence_page.main([str(matrix_dir), "--out", str(out)])
    page = out.read_text(encoding="utf-8")

    assert "<td>fail</td>" in page
    assert "evidence-macos-latest-3.10" in page
    assert '<span class="status">missing</span>' in page


def test_json_output_counts_and_page_path(matrix_dir, tmp_path):
    out = tmp_path / "evidence.html"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), str(matrix_dir), "--out", str(out), "--json"],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 1
    doc = json.loads(proc.stdout)
    assert doc == {"cells": 3, "passed": 1, "failed": 1, "missing": 1, "page": str(out)}


def test_all_pass_exits_zero_and_escapes_untrusted_text(tmp_path):
    ev_dir = tmp_path / "ev"
    ev_dir.mkdir()
    doc = _sample()
    doc["checks"][0]["log"] = "<script>alert(1)</script>"
    _write_cell(ev_dir, "evidence-windows-latest-3.13", doc)
    out = tmp_path / "evidence.html"

    assert evidence_page.main([str(ev_dir), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
