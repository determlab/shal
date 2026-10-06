"""MCP/CLI parity on limits refusal (issue #370, follow-up to shal#364/PR #366).

shal#364 made `shal call` refuse an out-of-limit actuator request as
`LimitsRejected` *before* it ever asks for approval. The MCP bridge enforces
the same limit at the same layer (the op wrapper in ``driver.py``, the one
place limits are checked — issue #10), so a gated op's over-limit call never
even reaches the deferring approver that would otherwise mint a ticket. This
file pins that: the MCP path and `shal call` agree on *whether* a value is
refused by limits, neither one sends anything to the device, and an
over-limit MCP call leaves no approval ticket behind.

Uses the shipped demo rig (`examples/demos/virtual-bench/bench.yaml`):
`psu.set_voltage` is a gated (`actuator`) op with a declared limit of 3.6 V.
30 V and 5 V are both outside it; 3.3 V is inside it.

Known gap, reported rather than fixed here (tests-only issue; `hal.py`'s
shared ``call_tool`` and `src/shal/mcp/bridge.py` are both out of scope — the
latter is a protected path): `shal call --json`'s refusal envelope adds
`sent` and a structured `error: {type, message, fix}`; the MCP bridge
returns `Hal.call_tool`'s raw dict for a `LimitError`, which has neither a
`sent` key nor a structured `error` (it is a bare string). The two paths
agree on the decision and on sending nothing — not yet on the exact shape of
how that refusal is reported. See `test_mcp_limits_refusal_envelope_gap_vs_cli`.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import shal
from shal.mcp import Bridge

_DEMO_BENCH = (Path(__file__).resolve().parents[1] / "examples" / "demos"
              / "virtual-bench" / "bench.yaml")
_TOOL = "psu__set_voltage"
_READ_TOOL = "psu__measure_voltage"


@pytest.fixture
def bridge():
    with shal.load(_DEMO_BENCH) as hal:
        yield Bridge(hal)


def _shal_call_json(volts, cwd) -> dict:
    r = subprocess.run(
        [sys.executable, "-m", "shal.cli", "call", str(_DEMO_BENCH),
         "psu", "set_voltage", str(volts), "--json"],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode != 0, r.stdout
    return json.loads(r.stdout)


# ---- over-limit MCP call: refused, no ticket, nothing sent -----------------------

@pytest.mark.parametrize("volts", [30, 5])
def test_over_limit_mcp_call_is_refused_with_no_ticket(bridge, volts):
    out = bridge.call(_TOOL, {"volts": volts})
    assert out["ok"] is False
    assert out["rejected"] == "limits"
    assert "approve_with" not in out                 # never offered for a limits refusal
    assert "status" not in out                       # no approval_required ticket was minted
    assert "approval_id" not in out
    assert bridge._pending == {}                      # the pending-ticket list is empty
    # end-to-end: the simulated PSU never moved off its initial 0.0 V
    assert bridge.call(_READ_TOOL, {}) == {"ok": True, "result": 0.0, "retries": 0}


# ---- in-range MCP call: still gated, exactly one ticket, nothing sent yet --------

def test_in_range_mcp_call_still_opens_one_ticket_and_sends_nothing(bridge):
    out = bridge.call(_TOOL, {"volts": 3.3})
    assert out["ok"] is False
    assert out["status"] == "approval_required"
    assert out["approval_id"]
    assert len(bridge._pending) == 1
    assert bridge.call(_READ_TOOL, {}) == {"ok": True, "result": 0.0, "retries": 0}  # not yet sent


# ---- parity: shal call and the MCP bridge agree on the refusal decision ---------

@pytest.mark.parametrize("volts", [30, 5, 3.3])
def test_mcp_and_cli_agree_on_the_refusal_for_the_same_value(tmp_path, bridge, volts):
    cli_out = _shal_call_json(volts, cwd=tmp_path)
    mcp_out = bridge.call(_TOOL, {"volts": volts})

    assert cli_out["ok"] is False
    assert mcp_out["ok"] is False
    assert cli_out["sent"] is False                   # shal call never sends on refusal

    if volts == 3.3:
        # in range: both entry points gate the actuator op, nothing is sent —
        # but only the MCP bridge can later resolve it (a ticket, not a flat
        # refusal); `shal call` has no --approve flag (AGENTS.md) and refuses
        # outright instead
        assert cli_out["rejected"] == "approval"
        assert cli_out["error"]["type"] == "ApprovalRequired"
        assert mcp_out["status"] == "approval_required"
        assert bridge._pending                        # a real, resolvable ticket
    else:
        # out of range: both refuse by LIMITS, before any approval step —
        # the one invariant this issue exists to pin
        assert cli_out["rejected"] == "limits"
        assert cli_out["error"]["type"] == "LimitsRejected"
        assert cli_out["error"]["fix"]
        assert mcp_out["rejected"] == "limits"
        assert bridge._pending == {}

    # neither path ever moved the simulated PSU off its initial 0.0 V
    assert bridge.call(_READ_TOOL, {}) == {"ok": True, "result": 0.0, "retries": 0}


def test_mcp_limits_refusal_envelope_gap_vs_cli(tmp_path, bridge):
    """Pins the known shape gap documented at the top of this file: both
    entry points refuse the same over-limit call and send nothing, but
    `shal call --json` additionally carries `sent: False` and a structured
    `error: {type, message, fix}`, while the MCP bridge's refusal (built from
    `Hal.call_tool`'s raw dict) has neither. Fixing it would mean touching
    `hal.py` and/or the protected `src/shal/mcp/bridge.py`, out of scope for
    this tests-only issue — reported in the PR instead."""
    cli_out = _shal_call_json(30, cwd=tmp_path)
    mcp_out = bridge.call(_TOOL, {"volts": 30})

    assert "sent" in cli_out and isinstance(cli_out["error"], dict)
    assert "sent" not in mcp_out                       # <- the gap
    assert isinstance(mcp_out["error"], str)            # <- the gap: not {type, message, fix}
