"""issue #472 DoD: no README or task text names a fault's signature.

A text may name a fault id (the answer enum stays visible), but it must not say
how a fault shows in a reading, which instrument shows it, or how many
instruments it takes -- an agent that reads that can answer from the text, not
from the instruments. How faults are injected lives in
``shal_arena/fault.py``'s docstrings, never in these texts.

Run this after editing ``arena/README.md``, the root ``AGENTS.md`` or any task's
``title`` / ``question.text``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

_ARENA_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _ARENA_ROOT.parent
_PKG = _ARENA_ROOT / "src" / "shal_arena"

# Signature phrases, kept next to the fault id (the ``card.yaml`` ``faults:``
# ids that ``fault.py`` realizes) whose signature they give away.
# _ANYWHERE: banned in any text. _NEAR_ID: banned only in a paragraph that
# also names a fault id (on their own they are ordinary driver guidance).
_ANYWHERE: dict[str, tuple[str, ...]] = {
    "ok": (),
    "low_voltage": ("shift",),
    "high_voltage": ("shift",),
    "noise": ("ripple",),
    "open": ("unplugged", "name the `open` fault"),
    "overheat": ("runs hot", "reads nominal", "all four instruments"),
    "broken_probe": ("probe is broken", "card is good"),
}
_NEAR_ID: dict[str, tuple[str, ...]] = {
    "open": ("looks like", "does not answer"),
}

_FAULT_IDS = tuple(_ANYWHERE)
# ids that are also plain English words count only when written as code
_PLAIN_WORDS = {"ok", "open", "noise"}
_ID_RE = re.compile("|".join(
    [rf"`{re.escape(i)}`" for i in _FAULT_IDS]
    + [rf"\b{re.escape(i)}\b" for i in _FAULT_IDS if i not in _PLAIN_WORDS]))

# The relay-rail paragraph as it stood on main before #472.
_OLD_RELAY_RAIL = """\
The `relay-rail` task adds a fourth instrument on a third protocol: `psu0`
and `dmm0` as above, plus `relay0` (a Modbus-framed relay switching the
card's own power, over `shal,sim-msg`, request/reply as plain dicts — no
`pymodbus`, no TCP) and `temp0` (an `sht31`-style temperature sensor on
`shal,sim-i2c`, probing the regulator) — with a fourth fault, `overheat`
(the regulator runs hot while the 3V3 rail still reads nominal, so only an
agent that reads all four instruments gets it right).
"""
_OLD_OPEN_NOTE = """\
**What `open` looks like.** One of the faults a card can hide is `open`: the
instrument simply does not answer — every hop to it raises, the same as a
cut cable.
"""


def signature_hits(text: str) -> list[str]:
    """Every signature phrase ``text`` holds, as ``"<fault id>: <phrase>"``."""
    hits: list[str] = []
    low = text.lower()
    for fault_id, phrases in _ANYWHERE.items():
        hits += [f"{fault_id}: {p}" for p in phrases if p.lower() in low]
    for paragraph in re.split(r"\n\s*\n", text):
        if not _ID_RE.search(paragraph):
            continue
        para = paragraph.lower()
        for fault_id, phrases in _NEAR_ID.items():
            hits += [f"{fault_id}: {p} (next to a fault id)"
                     for p in phrases if p in para]
    return sorted(set(hits))


def _texts() -> list[tuple[str, str]]:
    texts = [("arena/README.md", (_ARENA_ROOT / "README.md").read_text(encoding="utf-8")),
             ("AGENTS.md", (_REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8"))]
    for task in sorted((_PKG / "tasks").glob("*.yaml")):
        doc = yaml.safe_load(task.read_text(encoding="utf-8"))
        name = f"tasks/{task.name}"
        texts.append((f"{name} title", str(doc.get("title", ""))))
        texts.append((f"{name} question.text",
                      str((doc.get("question") or {}).get("text", ""))))
    return texts


@pytest.mark.parametrize(("where", "text"), _texts(), ids=lambda v: v if len(v) < 60 else "")
def test_text_names_no_fault_signature(where: str, text: str) -> None:
    assert signature_hits(text) == [], (
        f"{where} names a fault's signature: {signature_hits(text)} -- say what "
        "to do, never what a fault looks like (issue #472)")


def test_check_catches_the_old_relay_rail_paragraph() -> None:
    assert signature_hits(_OLD_RELAY_RAIL) == [
        "overheat: all four instruments", "overheat: reads nominal", "overheat: runs hot"]


def test_check_catches_the_old_open_note() -> None:
    assert signature_hits(_OLD_OPEN_NOTE) == [
        "open: does not answer (next to a fault id)", "open: looks like (next to a fault id)"]


def test_driver_guidance_without_a_fault_id_passes() -> None:
    assert signature_hits("If an instrument does not answer, let the exchange "
                          "raise; `measure` reports it as a normal failure.") == []


def test_phrase_list_covers_every_packaged_fault_id() -> None:
    card_ids = set()
    for card in (_PKG / "cards").glob("*.yaml"):
        doc = yaml.safe_load(card.read_text(encoding="utf-8"))
        card_ids |= {f["id"] for f in doc.get("faults", [])}
    assert card_ids and card_ids <= set(_FAULT_IDS), card_ids - set(_FAULT_IDS)
