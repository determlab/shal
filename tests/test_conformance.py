"""shal.conformance — the self-certification kit (DESIGN V2: 'product, not
scaffolding'). A generated driver passes check_driver() or it isn't done."""

import shal
from shal import conformance


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
