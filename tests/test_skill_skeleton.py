"""Issue #15 acceptance: a driver authored VERBATIM from the shal-build-driver
skeleton must pass `conformance.check_driver`. The skill's authoring guidance is a
closed contract (authors are told not to read SHAL source), so a skeleton that
fails Certify is a high-impact bug. This test pins the skeleton to the contract —
if the two drift again, CI fails.

The class body below is copied from `.claude/skills/shal-build-driver/SKILL.md`
(## Skeleton). Keep them identical.
"""
from shal import Driver, TemperatureSensor, conformance, idempotent, op, register
from shal.transport import ByteTransport, Read, Write


@register
class _SkeletonTemp(Driver, TemperatureSensor):
    # NOTE: compatible differs from the doc ('vendor,my-temp') only to avoid a
    # registry clash with other tests; everything else is the skeleton verbatim.
    compatible = "vendor,skeleton-temp"
    kind = ByteTransport
    llm_ready = True

    @idempotent
    @op("Read the current temperature. Call when you need a fresh reading.",
        unit="celsius", side_effect="none")
    def read_celsius(self) -> float:
        raw = self.bus.txn(self.addr, [Write(b"\x00"), Read(2)])
        return ((raw[0] << 4) | (raw[1] >> 4)) * 0.0625


def test_build_driver_skeleton_certifies():
    report = conformance.check_driver("vendor,skeleton-temp")
    assert report.ok, report.problems
    assert report.problems == []
