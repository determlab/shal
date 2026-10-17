"""Issue #412: the site workflow's guarantees, and the site build itself."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "site.yml"
SITE = ROOT / "site"


def _wf() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def test_permissions_are_minimal() -> None:
    wf = _wf()
    assert wf["permissions"] == {"contents": "read"}
    assert "permissions" not in wf["jobs"]["build"]
    assert wf["jobs"]["deploy"]["permissions"] == {"pages": "write", "id-token": "write"}


def test_no_secrets() -> None:
    assert "secrets" not in WORKFLOW.read_text(encoding="utf-8")


def test_nothing_enables_pages() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "configure-pages" not in text
    assert "enablement" not in text
    assert "/pages" not in text and "gh api" not in text


def test_every_action_pinned_by_full_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW.read_text(encoding="utf-8"))
    assert uses
    for ref in uses:
        assert "@v" not in ref, ref
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref


def test_deploy_job_runs_only_off_pull_requests() -> None:
    deploy = _wf()["jobs"]["deploy"]
    assert deploy["if"] == "github.event_name != 'pull_request'"
    assert deploy["concurrency"] == "pages"
    assert deploy["needs"] == "build"
    triggers = _wf()[True]  # PyYAML reads the bare key `on` as True
    assert "pull_request" in triggers and "workflow_dispatch" in triggers
    assert triggers["push"]["branches"] == ["main"]


def test_site_has_no_forms_prices_or_checkout() -> None:
    for path in SITE.rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text(encoding="utf-8").lower()
            for word in ("<form", "price", "checkout"):
                assert word not in text, f"{word!r} in {path}"


def test_build_writes_index_replay_and_card(tmp_path: Path) -> None:
    out = tmp_path / "site"
    proc = subprocess.run([sys.executable, str(SITE / "build.py"), "--out", str(out)],
                          capture_output=True, text=True, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
    index = (out / "index.html").read_text(encoding="utf-8")
    assert "uvx shal-arena demo" in index
    assert "Replay of a recorded run" in (out / "replay.html").read_text(encoding="utf-8")
    assert (out / "card.html").stat().st_size > 0
    hrefs = re.findall(r'(?:href|src)="([^"]+)"', index)
    local = [h for h in hrefs if not h.startswith(("http://", "https://", "#"))]
    assert {"replay.html", "card.html"} <= set(local)
    for href in local:
        assert (out / href).is_file(), href
