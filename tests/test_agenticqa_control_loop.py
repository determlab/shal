"""Tier 1 of agenticQA (#78): the device-agnostic release-acceptance control loop, run
hermetically in CI. One command (pytest), red/green, over the SAME gate a real agent
hits. Green on BOTH the deebot sim (gated actuator) and the sonos sim (benign write)
proves the loop is device-agnostic — driven entirely by per-device manuscripts.

The control loop + manuscripts live under dev/eval/ (never shipped in the wheel); add
them to sys.path so this test can import them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "dev" / "eval" / "agenticqa"))

from control_loop import load_manuscript, repo_root, run_control  # noqa: E402

_MANUSCRIPTS = _ROOT / "dev" / "eval" / "manuscripts"


def _manuscript(name: str) -> dict:
    return load_manuscript(_MANUSCRIPTS / f"{name}.yaml")


def test_repo_root_resolves() -> None:
    assert (repo_root() / "pyproject.toml").exists()


@pytest.mark.parametrize("device", ["deebot", "sonos"])
def test_approve_moves_the_device(device: str) -> None:
    """The headline: cold path -> drive one write -> verified state change. Device-agnostic
    (same call for deebot and sonos); pass/fail is the ground-truth read-back."""
    v = run_control(_manuscript(device), decision="approve")
    assert v["passed"], v["reason"]
    assert v["verdict"] == "PASS"
    assert v["state_after"] == v["expected_becomes"]
    assert v["state_after"] != v["state_before"]  # it genuinely moved


def test_deebot_exercises_the_gate() -> None:
    """deebot's start_cleaning is an actuator: the run must go THROUGH the approval gate
    (ticket issued, then approved) — not run free."""
    v = run_control(_manuscript("deebot"), decision="approve")
    assert v["passed"], v["reason"]
    assert v["gate_exercised"] is True
    assert v["gated_actual"] is True
    assert v["approval_id"]
    assert "requested" in v["audit_transitions"]
    assert "approved" in v["audit_transitions"]


def test_sonos_is_a_benign_write_not_a_gate() -> None:
    """sonos play is side_effect='write': it must NOT trip the gate (honest reporting of
    the op class — the loop proves it ran directly, no ticket)."""
    v = run_control(_manuscript("sonos"), decision="approve")
    assert v["passed"], v["reason"]
    assert v["gate_exercised"] is False
    assert v["gated_actual"] is False
    assert v["approval_id"] is None


def test_deny_path_leaves_device_unmoved() -> None:
    """A denied actuation must leave the device exactly where it was — verified by a
    read-back, and the refusal must be on the audit trail (#36)."""
    v = run_control(_manuscript("deebot"), decision="deny")
    assert v["passed"], v["reason"]
    assert v["state_after"] == v["state_before"]      # nothing moved
    assert v["state_after"] == "idle"
    assert "requested" in v["audit_transitions"]
    assert "denied" in v["audit_transitions"]


def test_catalog_downgrade_reads_as_red() -> None:
    """Anti-cheat (catalog): if the manuscript claims a gate but the live op is NOT
    actually gated (a downgrade), the loop must FAIL rather than pass."""
    m = _manuscript("sonos")          # sonos play is genuinely benign...
    m["expected"]["gated"] = True     # ...so claiming it's gated must read as red
    v = run_control(m, decision="approve")
    assert not v["passed"]
    assert "mismatch" in v["reason"] or "downgrad" in v["reason"]


def test_turning_the_gate_off_reads_as_red() -> None:
    """Anti-cheat (runtime): the strongest form. Turn the REAL gate off (free_writes) on a
    genuinely gated device — start_cleaning then runs with no ticket. The loop MUST go red,
    because a green that bypassed the gate is forbidden (#78). The device may still 'move',
    but skipping the approval interlock is itself the failure."""
    v = run_control(_manuscript("deebot"), decision="approve", free_writes=True)
    assert not v["passed"]
    assert "bypass" in v["reason"].lower()
    assert v["gate_exercised"] is False


def test_approve_run_fails_when_approver_denies() -> None:
    """Anti-cheat: the verdict must NOT silently adapt to the approver. If the approver
    refuses on an APPROVE run, that is a failure to verify a state change — not a pass via
    the deny branch — and the device must stay put."""
    v = run_control(_manuscript("deebot"), decision="approve", approver_fn=lambda t: "deny")
    assert not v["passed"]
    assert "refused" in v["reason"].lower()
    assert v["state_after"] in (None, v["state_before"])  # nothing moved


def test_no_op_actuation_reads_as_red() -> None:
    """Movement guard lives in the loop, not just the pytest: an approved op that leaves the
    device in its prior state cannot claim a VERIFIED state change. dock-while-docked is a
    30007 no-op in the sim, so becomes==before==idle must read red."""
    m = _manuscript("deebot")
    m["actuation"]["op"] = "dock"
    m["expected"] = {"read": "get_clean_state", "becomes": "idle", "gated": True}
    v = run_control(m, decision="approve")
    assert not v["passed"]
    assert "did not move" in v["reason"].lower()


def test_manuscript_validation_requires_actuation_op(tmp_path) -> None:
    """A missing actuation.op must fail loudly at load, not crash with a bare KeyError later."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: 1\ndevice: x\ntopology: t\nnode: n\ndrivers: []\n"
                   "liveness_reads: []\nactuation: {args: {}}\n"
                   "expected: {read: r, becomes: b, gated: false}\nteardown: []\n",
                   encoding="utf-8")
    with pytest.raises(ValueError, match="actuation.op"):
        load_manuscript(bad)
