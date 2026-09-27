"""The `shal.load(dict)` and `config:` examples in the docs run as written (#44).

Each test takes the first ```python block after a marker line in README.md or
src/shal/SDK.md (the page `shal docs --sdk` prints) and runs it. Change the
snippet and this test runs the new one; break it and this test fails.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
README = _ROOT / "README.md"
SDK = _ROOT / "src" / "shal" / "SDK.md"


def _snippet(doc: Path, marker: str) -> str:
    text = doc.read_text(encoding="utf-8")
    at = text.index(marker)  # ValueError here: the marker moved; update this test
    m = re.compile(r"^```python\n(.*?)^```$", re.M | re.S).search(text, at)
    assert m, f"{doc.name}: no ```python block after {marker!r}"
    return m.group(1)


def _run(code: str, capsys: pytest.CaptureFixture[str]) -> str:
    exec(compile(code, "<doc snippet>", "exec"), {"__name__": "__doc_snippet__"})
    return capsys.readouterr().out


@pytest.mark.parametrize("doc, marker", [
    (README, "## Load a topology from a dict"),
    (SDK, "**A topology can be a dict.**"),
])
def test_load_dict_snippet_reads_the_sim_sensor(
        doc: Path, marker: str, capsys: pytest.CaptureFixture[str]) -> None:
    code = _snippet(doc, marker)
    assert "shal.load(topology)" in code
    out = _run(code, capsys).strip()
    float(out)  # one live reading from the sim sensor


def test_config_snippet_reads_node_spec_config_with_env_resolved(
        monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("SITE", "unset")  # the snippet sets it; teardown removes it
    code = _snippet(SDK, "**A node's `config:` lives in `self.node.spec[\"config\"]`.**")
    assert 'self.node.spec.get("config", {})' in code
    assert _run(code, capsys).strip() == "hello from lab-3"
