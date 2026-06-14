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
