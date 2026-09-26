"""Human-in-the-loop actuation gate (issue #14).

The gate lives in the capability-wrapper, so these tests assert behavior through
the public surface (driver methods + hal.call_tool) — never the wrapper's guts.
The autouse AutoApprove fixture (conftest) is overridden per test where a real
policy decision is under test.
"""
import contextlib
import io

import pytest

import shal

# what the test driver's ops actually "did" — empty means nothing reached the device
RECEIVED: list[tuple[str, dict]] = []


@shal.register
class Rig(shal.Driver):
    """A device with one of each effect class, so the gate's selectivity is testable."""
    compatible = "test,approval-rig"
    kind = None

    @shal.op("Move the arm. Call to physically actuate.", side_effect="actuator")
    def move(self, dx: int) -> str:
        RECEIVED.append(("move", {"dx": dx}))
        return f"moved {dx}"

    @shal.op("Move the arm, but only within limits.", side_effect="actuator",
             params={"dx": {"maximum": 10}})
    def move_limited(self, dx: int) -> str:
        RECEIVED.append(("move_limited", {"dx": dx}))
        return f"moved {dx}"

    @shal.op("Set a register value (non-physical write).", side_effect="write")
    def set_reg(self, value: int) -> str:
        RECEIVED.append(("set_reg", {"value": value}))
        return f"reg={value}"

    @shal.op("Wipe stored maps (destructive config).", side_effect="config")
    def factory_reset(self) -> str:
        RECEIVED.append(("factory_reset", {}))
        return "wiped"

    @shal.op("Re-home the arm (idempotent physical motion).", side_effect="actuator")
    @shal.idempotent
    def home(self) -> str:
        RECEIVED.append(("home", {}))
        return "homed"

    @shal.op("Move with an optional speed (defaulted param).", side_effect="actuator")
    def move_at(self, dx: int, speed: int = 5) -> str:
        RECEIVED.append(("move_at", {"dx": dx, "speed": speed}))
        return f"moved {dx}@{speed}"

    @shal.op("Adjust the thing.")  # author FORGOT side_effect (issue #19 repro)
    def unclassified(self, x: int) -> str:
        RECEIVED.append(("unclassified", {"x": x}))
        return f"did {x}"

    @shal.op("Read the sensor.", side_effect="none")
    @shal.idempotent
    def read(self) -> int:
        RECEIVED.append(("read", {}))
        return 42


_YAML = ("shal_version: 1\n"
         "root:\n"
         "  rig: {id: rig, driver: 'test,approval-rig', address: 1}\n")


@pytest.fixture
def hal(tmp_path):
    RECEIVED.clear()
    p = tmp_path / "s.yaml"
    p.write_text(_YAML, encoding="utf-8")
    with shal.load(p) as h:
        yield h


class Spy:
    """Records every request and answers with a fixed verdict."""
    def __init__(self, allow: bool) -> None:
        self.allow = allow
        self.seen: list[shal.ApprovalRequest] = []

    def approve(self, request):
        self.seen.append(request)
        return self.allow


# ---- the core guarantee: an actuator is gated, both call paths --------------------

def test_actuator_denied_sends_nothing_raw_path(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(5)
    assert RECEIVED == []  # the device never saw the command


def test_actuator_denied_via_tool_surface(hal):
    with shal.approver(shal.DenyAll()):
        out = hal.call_tool("rig__move", {"dx": 5})
    assert out["ok"] is False and out["rejected"] == "approval"
    assert "delivered" not in out  # nothing was sent -> no delivery ambiguity
    assert RECEIVED == []


def test_actuator_allowed_executes(hal):
    with shal.approver(shal.AutoApprove()):
        assert hal.get_device("rig").move(5) == "moved 5"
    assert RECEIVED == [("move", {"dx": 5})]


# ---- fail-closed: an un-annotated state-changer is gated (issue #19) --------------

def test_unannotated_state_changer_is_gated(hal):
    # author wrote @op but forgot side_effect; the safe default must GATE it
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").unclassified(1)
    assert RECEIVED == []  # nothing reached the device


def test_unannotated_state_changer_infers_actuator(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").unclassified(2)
    (req,) = spy.seen
    assert req.side_effect == "actuator"  # fail-closed inference, not ungated "write"
    assert RECEIVED == [("unclassified", {"x": 2})]


# ---- selectivity: only actuators are gated ---------------------------------------

def test_write_op_is_not_gated(hal):
    with shal.approver(shal.DenyAll()):  # would block an actuator, but this is a write
        assert hal.get_device("rig").set_reg(7) == "reg=7"
    assert RECEIVED == [("set_reg", {"value": 7})]


def test_read_op_is_not_gated(hal):
    with shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").read() == 42
    assert RECEIVED == [("read", {})]


# ---- ordering: limits win, the approver is never asked an impossible question -----

def test_limits_checked_before_approval(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        with pytest.raises(shal.LimitError):
            hal.get_device("rig").move_limited(99)   # over the declared maximum
    assert spy.seen == []        # approver never consulted for an impossible op
    assert RECEIVED == []        # and nothing was sent


# ---- the request the host receives -----------------------------------------------

def test_request_carries_op_path_and_params(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").move(3)
    (req,) = spy.seen
    assert req.op == "move" and req.id == "rig"
    assert req.params == {"dx": 3}
    assert req.side_effect == "actuator"
    assert req.path.endswith("/rig")


# ---- the shipped default is safe -------------------------------------------------

def test_console_approver_denies_when_not_a_tty():
    import io
    approver = shal.ConsoleApprover(stream=io.StringIO())  # not a TTY
    req = shal.ApprovalRequest(op="move", path="/rig", id="rig",
                               side_effect="actuator", params={"dx": 1}, txn="----")
    assert approver.approve(req) is False


def test_console_approver_allows_on_yes():
    class TTY(io.StringIO):
        def isatty(self):
            return True
    approver = shal.ConsoleApprover(stream=TTY(), prompt=lambda _: "y")
    req = shal.ApprovalRequest(op="move", path="/rig", id="rig",
                               side_effect="actuator", params={}, txn="----")
    assert approver.approve(req) is True


# ---- every decision is audited ---------------------------------------------------

@pytest.fixture
def audit_records():
    import logging
    records = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level=logging.INFO)
    audit = logging.getLogger("shal.audit")  # propagate=False -> needs its own handler
    audit.addHandler(handler)
    audit.setLevel(logging.INFO)
    yield records
    audit.removeHandler(handler)
    audit.setLevel(logging.NOTSET)


def test_denied_actuation_is_audited(hal, audit_records):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(1)
    denied = [r for r in audit_records if r.outcome == "denied"]
    assert denied and denied[0].op == "move" and denied[0].event == "audit"


def test_approved_actuation_is_audited(hal, audit_records):
    with shal.approver(shal.AutoApprove()):
        hal.get_device("rig").move(1)
    outcomes = [r.outcome for r in audit_records if r.op == "move"]
    # the decision is recorded BEFORE the I/O outcome (order: approval -> I/O)
    assert outcomes == ["approved", "ok"]


# ---- config (destructive) ops are gated too (issue #14 ADR: actuator AND config) -

def test_config_op_is_gated_and_denied(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").factory_reset()
    assert RECEIVED == []  # destructive config never reached the device


def test_config_op_allowed_executes(hal):
    with shal.approver(shal.AutoApprove()):
        assert hal.get_device("rig").factory_reset() == "wiped"
    assert RECEIVED == [("factory_reset", {})]


# ---- an @idempotent actuator is still gated AND its decision is audited (fix #1) --

def test_idempotent_actuator_is_gated(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").home()
    assert RECEIVED == []


def test_idempotent_actuator_decision_is_audited(hal, audit_records):
    # `home` is @idempotent AND gated — the decision must log (the label decides, #194)
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").home()
    denied = [r for r in audit_records if r.op == "home" and r.outcome == "denied"]
    assert denied, "an idempotent actuator's approval decision must be audited"


# ---- the request carries defaulted params (apply_defaults path) -------------------

def test_request_includes_defaulted_params(hal):
    spy = Spy(allow=True)
    with shal.approver(spy):
        hal.get_device("rig").move_at(3)  # speed defaults to 5
    (req,) = spy.seen
    assert req.params == {"dx": 3, "speed": 5}


# ---- the shipped DEFAULT policy denies end-to-end through the wrapper (fix #4) ----

def test_default_console_policy_denies_headless_through_wrapper(hal):
    # the real default approver (ConsoleApprover), headless (non-TTY), must deny an
    # actuator call THROUGH the wrapper — guards against the gate being skipped or
    # the default flipped. (The autouse AutoApprove fixture is overridden here.)
    with shal.approver(shal.ConsoleApprover(stream=io.StringIO())):
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(1)
    assert RECEIVED == []


def test_default_approver_is_console_when_context_unset():
    # with no policy installed in this context, the fallback is ConsoleApprover
    token = shal.approval._current.set(None)
    try:
        assert isinstance(shal.get_approver(), shal.ConsoleApprover)
    finally:
        shal.approval._current.reset(token)


# ---- a no-approver denial says how to approve, with reason "no-approver" (#186) ----

# copied byte-for-byte from shal#186's body; the shipped constant must equal it
_ISSUE_186_LINE = ("no approver is set and stdin is not a terminal. In Python: "
                   "shal.approver(...) — AutoApprove() for a sim, or an approver "
                   "that asks a person; under an agent host: shal mcp.")


def test_no_approver_message_is_the_issue_line_verbatim():
    from shal.errors import HOW_TO_APPROVE_LINE, NO_APPROVER_MESSAGE
    assert NO_APPROVER_MESSAGE.encode("utf-8") == _ISSUE_186_LINE.encode("utf-8")
    assert NO_APPROVER_MESSAGE.endswith(HOW_TO_APPROVE_LINE)


class _Stdin(io.StringIO):
    def __init__(self, tty):
        super().__init__()
        self._tty = tty

    def isatty(self):
        return self._tty


def test_no_approver_headless_denial_carries_the_line_and_reason(hal, monkeypatch):
    from shal.errors import NO_APPROVER_MESSAGE
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    token = shal.approval._current.set(None)  # no approver set: the shipped default
    try:
        with pytest.raises(shal.ApprovalDenied) as ei:
            hal.get_device("rig").move(1)
    finally:
        shal.approval._current.reset(token)
    assert ei.value.reason == "no-approver"
    assert str(ei.value).endswith(NO_APPROVER_MESSAGE)
    assert str(ei.value).count("shal mcp") == 1       # one hint, not two
    assert RECEIVED == []


def test_no_approver_with_a_tty_still_prompts(hal, monkeypatch):
    asked = []
    monkeypatch.setattr("sys.stdin", _Stdin(tty=True))
    # the shipped default's prompt (`input`, bound at construction) is the one asked
    monkeypatch.setattr(shal.approval._DEFAULT, "_prompt",
                        lambda banner: asked.append(banner) or "n")
    token = shal.approval._current.set(None)
    try:
        with pytest.raises(shal.ApprovalDenied) as ei:
            hal.get_device("rig").move(1)
    finally:
        shal.approval._current.reset(token)
    assert len(asked) == 1 and "Allow this actuation?" in asked[0]
    assert ei.value.reason is None                    # a person said no
    assert RECEIVED == []


def test_other_denials_have_no_reason(hal):
    with shal.approver(shal.DenyAll()):
        with pytest.raises(shal.ApprovalDenied) as ei:
            hal.get_device("rig").move(1)
    assert ei.value.reason is None
    assert "to approve:" in str(ei.value)             # the general #156 hint stays


def test_no_approver_denial_pickle_round_trip_keeps_reason_and_one_hint():
    import pickle

    from shal.errors import NO_APPROVER_MESSAGE
    err = shal.ApprovalDenied("/rig  move denied", op="move", reason="no-approver")
    back = pickle.loads(pickle.dumps(err))
    assert back.reason == "no-approver"
    assert str(back) == str(err)
    assert str(back).count(NO_APPROVER_MESSAGE) == 1 and "to approve:" not in str(back)


def test_call_tool_refusal_carries_reason(hal, monkeypatch):
    """#186: the call_tool dict (what --json / MCP consumers see) carries `reason`."""
    monkeypatch.setattr("sys.stdin", _Stdin(tty=False))
    token = shal.approval._current.set(None)  # no approver set, headless
    try:
        out = hal.call_tool("rig__move", {"dx": 1})
    finally:
        shal.approval._current.reset(token)
    assert out["ok"] is False and out["rejected"] == "approval"
    assert out["reason"] == "no-approver"
    with shal.approver(shal.DenyAll()):
        out = hal.call_tool("rig__move", {"dx": 1})
    assert out["rejected"] == "approval" and out["reason"] is None
    assert RECEIVED == []


# ---- WHICH effects are gated is a host policy too (issue #114) --------------------
# The Approver answers "who decides"; the gated set answers "which effects even
# reach that decision". The default must be byte-identical for a consumer that
# never touches this API — the tests ABOVE are the proof of that and are unchanged.

def test_default_gated_set_is_actuator_and_config():
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})


def test_default_gated_set_when_context_unset():
    token = shal.driver._current_gated.set(None)
    try:
        assert shal.get_gated_effects() == frozenset({"actuator", "config"})
    finally:
        shal.driver._current_gated.reset(token)


def test_seated_policy_gates_a_write_and_denyall_denies(hal):
    """The DoD case: a host seats {write, actuator, config}; a benign write now
    consults the approver and DenyAll stops it pre-I/O."""
    with shal.gated_effects({"write", "actuator", "config"}):
        with shal.approver(shal.DenyAll()):
            with pytest.raises(shal.ApprovalDenied):
                hal.get_device("rig").set_reg(7)
    assert RECEIVED == []  # nothing reached the device


def test_seated_policy_write_reaches_the_approver_as_write(hal):
    spy = Spy(allow=True)
    with shal.gated_effects({"write", "actuator", "config"}), shal.approver(spy):
        assert hal.get_device("rig").set_reg(7) == "reg=7"
    (req,) = spy.seen
    assert req.side_effect == "write" and req.op == "set_reg"
    assert RECEIVED == [("set_reg", {"value": 7})]


def test_seated_policy_also_gates_the_tool_surface(hal):
    """Same gate, both call paths — the policy is not a raw-path-only thing."""
    with shal.gated_effects({"write", "actuator", "config"}):
        with shal.approver(shal.DenyAll()):
            out = hal.call_tool("rig__set_reg", {"value": 7})
    assert out["ok"] is False and out["rejected"] == "approval"
    assert RECEIVED == []


def test_policy_can_narrow_as_well_as_widen(hal):
    """A gated set is a real input, not a one-way ratchet: dropping "actuator"
    frees an actuator op that DenyAll would otherwise refuse."""
    with shal.gated_effects({"config"}), shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").move(5) == "moved 5"
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").factory_reset()
    assert RECEIVED == [("move", {"dx": 5})]


def test_policy_is_restored_on_scope_exit(hal):
    with shal.gated_effects({"write"}):
        assert shal.get_gated_effects() == frozenset({"write"})
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})
    # ...and the default gate is back in force
    with shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").set_reg(1) == "reg=1"   # write: ungated again
        with pytest.raises(shal.ApprovalDenied):
            hal.get_device("rig").move(1)                    # actuator: gated again


def test_set_gated_effects_token_resets(hal):
    token = shal.set_gated_effects({"write"})
    try:
        assert shal.get_gated_effects() == frozenset({"write"})
    finally:
        shal.driver.reset_gated_effects(token)
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})


# ---- invalid input fails AT THE CALL SITE, not silently at the next op ------------

@pytest.mark.parametrize("bad", [{"wrtie"}, {"actuator", "nope"}, {"ACTUATOR"}, {""}])
def test_unknown_effect_name_raises_at_the_call_site(hal, bad):
    with pytest.raises(ValueError, match="unknown side_effect"):
        shal.set_gated_effects(bad)
    # the policy is untouched and the next op behaves exactly as before
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})
    with shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").set_reg(3) == "reg=3"


def test_unknown_effect_name_raises_before_entering_the_with_block():
    entered = False
    with pytest.raises(ValueError, match="unknown side_effect"):
        with shal.gated_effects({"actuator", "bogus"}):
            entered = True          # pragma: no cover
    assert entered is False
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})


def test_none_is_rejected_outright(hal):
    """Gating a read has no meaning, and advertising it would be self-contradictory
    (readOnlyHint AND destructiveHint on the same op). Refuse it at the call site."""
    for bad in ({"none"}, {"none", "actuator"}):
        with pytest.raises(ValueError, match="cannot include 'none'"):
            shal.set_gated_effects(bad)
        with pytest.raises(ValueError, match="cannot include 'none'"):
            with shal.gated_effects(bad):
                pass                # pragma: no cover
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})
    with shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").read() == 42   # reads stay free, always


def test_empty_policy_gates_nothing(hal):
    """An explicit opt-out is legal (and must be explicit) — unlike "none", an
    empty set is a coherent statement: gate nothing."""
    with shal.gated_effects(set()), shal.approver(shal.DenyAll()):
        assert hal.get_device("rig").move(5) == "moved 5"
    assert RECEIVED == [("move", {"dx": 5})]


def test_catalog_hints_follow_the_seated_policy():
    """``registry.catalog()`` is the OTHER advertiser of the gated set (the authoring
    surface). It must read the live policy too — otherwise it describes a `write` as
    free while the runtime gate stops it for a human."""
    def hints(policy=None):
        scope = contextlib.nullcontext() if policy is None else shal.gated_effects(policy)
        with scope:
            return {o["name"]: o["annotations"]["destructiveHint"]
                    for o in shal.catalog("test,approval-rig")["ops"]}

    assert hints()["move"] is True and hints()["set_reg"] is False     # shipped default
    seated = hints({"write", "actuator", "config"})
    assert seated["move"] is True and seated["set_reg"] is True
    narrowed = hints({"write"})
    assert narrowed["move"] is False and narrowed["set_reg"] is True
    assert hints()["set_reg"] is False                                 # policy popped


def test_gated_set_is_not_inherited_by_a_new_thread(hal):
    """The documented ContextVar caveat, identical to set_approver: a newly spawned
    OS thread does NOT inherit the policy and falls back to the safe default."""
    import threading
    seen: list[frozenset] = []
    with shal.gated_effects({"write"}):
        t = threading.Thread(target=lambda: seen.append(shal.get_gated_effects()))
        t.start()
        t.join()
    assert seen == [frozenset({"actuator", "config"})]   # the safe default, not {"write"}


# ---- the gated set decides who is ASKED; the audit follows the LABEL (#194) -------

def test_widening_the_gated_set_does_not_change_what_is_audited(hal, audit_records):
    """A `write` op is audited under the default (label: not a read) and stays
    audited under a widened set — the policy only ADDS the approval decision to the
    record, it never adds or removes the op's own outcome record."""
    hal.get_device("rig").set_reg(1)
    default = [r.outcome for r in audit_records if r.op == "set_reg"]
    audit_records.clear()
    with shal.gated_effects({"write", "actuator", "config"}):
        hal.get_device("rig").set_reg(2)                   # AutoApprove (conftest)
    widened = [r.outcome for r in audit_records if r.op == "set_reg"]
    assert default == ["ok"]
    assert widened == ["approved", "ok"]


def test_a_write_denied_under_a_widened_set_is_audited_as_a_write(hal, audit_records):
    with shal.gated_effects({"write", "actuator", "config"}):
        with shal.approver(shal.DenyAll()):
            with pytest.raises(shal.ApprovalDenied) as ei:
                hal.get_device("rig").set_reg(7)
    assert ei.value.side_effect == "write"
    (rec,) = [r for r in audit_records if r.op == "set_reg"]
    assert rec.outcome == "denied" and rec.side_effect == "write"
    assert RECEIVED == []


def test_narrowing_the_gated_set_still_audits_the_actuator(hal, audit_records):
    """An `actuator` op freed by a narrowed set is not asked, but it is still on the
    record: audit is keyed on the label, never on the policy."""
    with shal.gated_effects({"config"}), shal.approver(shal.DenyAll()):
        hal.get_device("rig").move(3)
    assert [r.outcome for r in audit_records if r.op == "move"] == ["ok"]


def test_the_description_follows_the_seated_policy(hal):
    """`_describe`'s "needs a person's approval" sentence is advertising too."""
    def desc():
        return next(t["description"] for t in hal.tool_schemas()
                    if t["name"] == "rig__home")
    assert "needs a person's approval" in desc()
    with shal.gated_effects({"write"}):
        assert "needs a person's approval" not in desc()


def test_gated_set_is_isolated_between_contexts():
    """A ContextVar, not a global: a policy seated inside a copied context does not
    leak out of it."""
    import contextvars
    ctx = contextvars.copy_context()
    ctx.run(shal.set_gated_effects, {"write"})
    assert ctx.run(shal.get_gated_effects) == frozenset({"write"})
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})


def test_nested_scopes_restore_in_order():
    with shal.gated_effects({"write"}):
        with shal.gated_effects({"config"}):
            assert shal.get_gated_effects() == frozenset({"config"})
        assert shal.get_gated_effects() == frozenset({"write"})
    assert shal.get_gated_effects() == frozenset({"actuator", "config"})
