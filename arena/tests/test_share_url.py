"""issue #413: the result card's Share link opens a prefilled GitHub issue
form. The URL carries only task, seed, score and record sha256; the product
makes no network call (the link is the person's own click)."""
from __future__ import annotations

import contextlib
import getpass
import html
import io
import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import yaml

from shal_arena.replay.card import SHARE_BUTTON_TEXT, SHARE_NOTE, load_card_data, share_url

REPO_ROOT = Path(__file__).resolve().parents[2]
FIELD_IDS = ("task", "seed", "score", "sha256")


def _demo(tmp_path: Path) -> dict:
    """`shal-arena demo --json --pause 0`: `cli._cmd_demo` calls exactly
    `demo.run_story`, run here in this process (the code under test)."""
    from shal_arena import demo

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = demo.run_story(pause=0, json_mode=True)
    assert code == 0
    return json.loads(out.getvalue())


def _card_link(card_html: str) -> str:
    m = re.search(r'<a id="share-link" href="([^"]*)"', card_html)
    assert m, "no share link on the card"
    return html.unescape(m.group(1))


def test_share_url_from_a_real_demo_record(tmp_path: Path) -> None:
    doc = _demo(tmp_path)
    url = doc["share_url"]
    card_html = Path(doc["card_path"]).read_text(encoding="utf-8")

    assert url == _card_link(card_html)
    assert len(url) < 2000
    assert url.startswith("https://github.com/determlab/shal/issues/new?")

    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    assert query["template"] == ["arena-result.yml"]
    assert "body" not in query and "body=" not in url
    assert "title" in query
    for field in FIELD_IDS:
        assert len(query[field]) == 1 and query[field][0], field

    # the URL is built from the real record's score
    run_id = next(s for s in doc["steps"] if s["step"] == "arena_result_card")["result"]["run_id"]
    state_dir = Path(doc["state_dir"]) / "hard"
    score = load_card_data(run_id, state_dir=state_dir).score
    assert share_url(score) == url
    assert query["task"] == [score["task_id"]]
    assert query["seed"] == [str(score["seed"])]
    assert query["sha256"] == [score["record_sha256"]]

    forbidden = ["/", "\\", str(Path.home()), getpass.getuser(), str(tmp_path),
                 doc["state_dir"]]
    for name, values in query.items():
        if name == "template":
            continue
        for value in values:
            assert not re.search(r"(?<![A-Za-z])[A-Za-z]:(?![ ])", value), (name, value)
            for bad in forbidden:
                assert bad not in value, (name, value, bad)


def test_card_carries_the_exact_share_wording(tmp_path: Path) -> None:
    doc = _demo(tmp_path)
    card_html = Path(doc["card_path"]).read_text(encoding="utf-8")
    assert ("Talk to us for 15 minutes about how you test "
            "(opens a GitHub issue in your browser)") in card_html
    assert ("Nothing is sent until you press submit on GitHub. The issue is public and "
            "under your GitHub name. You can edit or delete it yourself at any time."
            ) in card_html
    assert SHARE_BUTTON_TEXT in card_html and SHARE_NOTE in card_html


def test_issue_templates() -> None:
    tdir = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
    result = yaml.safe_load((tdir / "arena-result.yml").read_text(encoding="utf-8"))
    assert "arena-result" in result["labels"]
    ids = {b["id"] for b in result["body"] if "id" in b}
    assert set(FIELD_IDS) <= ids
    request = yaml.safe_load((tdir / "driver-request.yml").read_text(encoding="utf-8"))
    assert request["name"] and request["description"] and request["body"]
