"""shal.conformance — the self-certification kit (DESIGN V2: 'product, not
scaffolding'). A generated driver passes check_driver() or it isn't done."""

from pathlib import Path

import pytest

import shal
from shal import conformance

HERE = Path(__file__).parent


@shal.register
class _GoodPsu(shal.Driver):
    compatible = "test,conf-good"
    kind = None
    llm_ready = True

    @shal.idempotent
    @shal.op("Read the output voltage now.", unit="volt", side_effect="none")
    def read_voltage(self) -> float:
        return 1.5

    @shal.op("Set the output voltage.", unit="volt", side_effect="write",
             params={"volts": {"minimum": 0.0, "maximum": 30.0}})
    def set_voltage(self, volts: float) -> str:
        return "ok"


@shal.register
class _SloppyPsu(shal.Driver):
    """Write op with a numeric param and NO declared limit -> warning."""

    compatible = "test,conf-sloppy"
    kind = None
    llm_ready = True

    @shal.op("Set the output voltage.", unit="volt", side_effect="write")
    def set_voltage(self, volts: float) -> str:
        return "ok"


GOOD_YAML = ("shal_version: 1\n"
             "root:\n"
             "  psu: {id: psu, driver: 'test,conf-good', address: 1}\n")


def test_good_driver_certifies(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(GOOD_YAML, encoding="utf-8")
    report = conformance.check_driver("test,conf-good", topology=p)
    assert report.ok, report.problems
    assert report.problems == []
    # the live probe actually ran: limits enforcement + audit were exercised
    assert any("limits" in c for c in report.checked)
    assert any("audit" in c for c in report.checked)


def test_unbounded_numeric_write_param_is_warned():
    report = conformance.check_driver("test,conf-sloppy")
    assert report.ok                       # warning, not failure: bool(on) ops exist
    assert any("set_voltage" in w and "volts" in w for w in report.warnings)


@shal.register
class _GoodArm(shal.Driver):
    """A driver whose only state-changing op is GATED (actuator) — exercises the
    approval interlock inside conformance (issue #14)."""

    compatible = "test,conf-actuator"
    kind = None
    llm_ready = True

    @shal.idempotent
    @shal.op("Read arm position now.", side_effect="none")
    def read_position(self) -> int:
        return 0

    @shal.op("Move the arm. Physical motion.", side_effect="actuator")
    def move(self, dx: int) -> str:
        return f"moved {dx}"


ACTUATOR_YAML = ("shal_version: 1\n"
                 "root:\n"
                 "  arm: {id: arm, driver: 'test,conf-actuator', address: 1}\n")


def test_gated_driver_certifies_headless(tmp_path):
    """check_driver must certify a driver whose audited op is gated, even when the
    ambient policy DENIES — its internal AutoApprove wrap is what lets the probe
    reach real I/O. Guards against a revert of that wrap (which would silently
    'pass' on a *denied* audit record instead of a real one)."""
    import logging

    p = tmp_path / "s.yaml"
    p.write_text(ACTUATOR_YAML, encoding="utf-8")

    records: list[logging.LogRecord] = []

    class Collect(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = Collect(level=logging.INFO)
    audit = logging.getLogger("shal.audit")
    audit.addHandler(handler)
    prior = audit.level
    audit.setLevel(logging.INFO)
    try:
        # ambient policy denies (simulate headless): only conformance's own
        # AutoApprove wrap should let the actuator probe through.
        with shal.approver(shal.DenyAll()):
            report = conformance.check_driver("test,conf-actuator", topology=p)
    finally:
        audit.removeHandler(handler)
        audit.setLevel(prior)

    assert report.ok, report.problems
    assert any("audit" in c for c in report.checked)
    move_outcomes = [getattr(r, "outcome", None) for r in records
                     if getattr(r, "op", None) == "move"]
    assert "ok" in move_outcomes        # the op actually EXECUTED...
    assert "denied" not in move_outcomes  # ...not merely denied-and-logged


def test_missing_op_metadata_is_a_problem():
    @shal.register
    class _NoMeta(shal.Driver):  # noqa
        compatible = "test,conf-nometa"
        kind = None
        # llm_ready NOT set, no @op metadata

        def do_thing(self) -> int:
            return 1

    report = conformance.check_driver("test,conf-nometa")
    assert not report.ok
    assert any("llm_ready" in p or "@shal.op" in p for p in report.problems)


# ---- freshness probe (D12, issue #108) -------------------------------------------

@shal.register
class _FreshReader(shal.Driver):
    """D12-compliant: a HopError from the bus reaches the caller untouched."""

    compatible = "test,conf-fresh-good"
    kind = shal.ByteTransport
    llm_ready = True

    @shal.idempotent
    @shal.op("Read now.", side_effect="none")
    def read_value(self) -> int:
        raw = self.bus.txn(self.addr, [shal.Read(1)])
        return raw[0]


@shal.register
class _StaleReader(shal.Driver):
    """The anti-pattern D12 forbids: a non-delivering hop is swallowed and a
    stale default returned instead of raising."""

    compatible = "test,conf-fresh-bad"
    kind = shal.ByteTransport
    llm_ready = True

    @shal.idempotent
    @shal.op("Read now.", side_effect="none")
    def read_value(self) -> int:
        try:
            raw = self.bus.txn(self.addr, [shal.Read(1)])
        except shal.HopError:
            return 0  # BAD — never do this; see SDK.md 1b
        return raw[0]


FRESH_YAML = ("shal_version: 1\n"
              "root:\n"
              "  bench:\n"
              "    driver: shal,sim-i2c\n"
              "    address: sim0\n"
              "    children:\n"
              "      dev: {{id: dev, driver: '{compatible}', address: 80}}\n")


def test_freshness_probe_certifies_a_driver_that_raises_on_no_answer(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(FRESH_YAML.format(compatible="test,conf-fresh-good"), encoding="utf-8")
    report = conformance.check_driver("test,conf-fresh-good", topology=p)
    assert report.ok, report.problems
    assert any("freshness" in c for c in report.checked)


def test_freshness_probe_catches_a_stale_default_on_no_answer(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(FRESH_YAML.format(compatible="test,conf-fresh-bad"), encoding="utf-8")
    report = conformance.check_driver("test,conf-fresh-bad", topology=p)
    assert not report.ok
    assert any("D12" in prob for prob in report.problems)


def test_freshness_probe_on_the_real_i2c_stack():
    """The end-to-end regression this issue was filed over: tmp102 on the core
    sim-i2c bus must raise HopError, not return/crash, when the bus can't
    deliver."""
    report = conformance.check_driver("ti,tmp102", topology=HERE / "setup_sim.yaml")
    assert report.ok, report.problems
    assert any("freshness" in c for c in report.checked)


# ---- issue #162/#194: an op with no side_effect is warned (legal, gated, but silent) ----

def _omission_warning(op: str) -> str:
    """The ONE text for any op with no side_effect, @idempotent or not (#194)."""
    return (f'{op}: no side_effect declared; treated as actuator (gated, audited). '
            f'Declare it — "none" for a read, "write" for a benign, reversible '
            f'change, "config"/"actuator" for a gated one.')


UNLABELLED_WARNING = _omission_warning("start")


@shal.register
class _UnlabelledPump(shal.Driver):
    """One @op without side_effect; every other op declares its label."""

    compatible = "test,conf-unlabelled"
    kind = None
    llm_ready = True

    @shal.idempotent
    @shal.op("Read whether the pump runs now.", side_effect="none")
    def running(self) -> bool:
        return False

    @shal.op("Start the pump.")   # the author forgot side_effect
    def start(self) -> str:
        return "started"


UNLABELLED_YAML = ("shal_version: 1\n"
                   "root:\n"
                   "  pump: {id: pump, driver: 'test,conf-unlabelled', address: 1}\n")


def test_an_op_without_side_effect_is_warned_word_for_word(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(UNLABELLED_YAML, encoding="utf-8")
    report = conformance.check_driver("test,conf-unlabelled", topology=p)
    assert report.warnings == [UNLABELLED_WARNING]
    assert report.problems == [] and report.ok   # a warning, never a problem


def test_the_unlabelled_op_still_loads_and_runs_gated(tmp_path):
    p = tmp_path / "s.yaml"
    p.write_text(UNLABELLED_YAML, encoding="utf-8")
    with shal.load(p) as hal:
        pump = hal.get_device("pump")
        with shal.approver(shal.DenyAll()):
            with pytest.raises(shal.ApprovalDenied):
                pump.start()
        with shal.approver(shal.AutoApprove()):
            assert pump.start() == "started"


def test_a_driver_that_declares_every_label_gets_no_such_warning():
    for compatible in ("test,conf-good", "test,conf-actuator", "test,conf-sloppy"):
        report = conformance.check_driver(compatible)
        assert not any("no side_effect declared" in w for w in report.warnings), (
            compatible, report.warnings)


# ---- issue #194: an @idempotent op with no side_effect gets the SAME warning ----
# (#183's separate problem is gone: the runtime now gates the op, so no omission
# is left that runs ungated)

def _setpoint_driver(label: dict) -> type:
    class _Setpoint(shal.Driver):
        compatible = ""   # unregistered: skip the catalog, check the ops
        kind = None
        llm_ready = True

        @shal.idempotent
        @shal.op("Set the output to an absolute level.", **label)
        def set_level(self) -> str:
            return "set"
    return _Setpoint


def test_an_idempotent_op_without_side_effect_gets_the_one_warning():
    report = conformance.check_driver(_setpoint_driver({}))
    assert report.warnings == [_omission_warning("set_level")]
    assert report.problems == [] and report.ok   # a warning, never a problem


def test_a_device_op_with_no_op_at_all_gets_the_one_warning_too():
    class _Bare(shal.Driver):
        compatible = ""
        kind = None
        llm_ready = True

        @shal.idempotent
        def level(self) -> int:
            return 1

    report = conformance.check_driver(_Bare)
    assert report.warnings == [_omission_warning("level")]
    assert report.problems == ["level: missing @shal.op description"]


@pytest.mark.parametrize("side_effect", ["none", "write"])
def test_the_same_op_with_side_effect_declared_is_clean(side_effect):
    report = conformance.check_driver(_setpoint_driver({"side_effect": side_effect}))
    assert report.problems == [] and report.warnings == [], report


def test_runtime_inference_is_actuator_for_the_unlabelled_idempotent_op():
    # one decorator, one meaning (#194): @idempotent never declares a read
    fn = _setpoint_driver({}).capability_ops()["set_level"]
    assert shal.driver.inferred_side_effect(fn) == "actuator"

