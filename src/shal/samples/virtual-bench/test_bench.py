"""The virtual bench (issue #305, T8): set the PSU, measure it with the DMM,
``check()`` it within 2%. One pytest-shal test, one record per run.

``set_voltage`` is a gated ``actuator`` op (D24): pytest-shal's own
``--shal-approve allow`` only ever approves its bundled ``--shal-setup sim``
topology, not a project's own setup file like ``bench.yaml`` (it refuses with
a usage error otherwise). So this test approves that one call itself, the
same ``with shal.approver(shal.AutoApprove()):`` idiom the rest of this repo's
sim/CI tests use — scoped to this call only, never process-wide, and it nests
correctly inside pytest-shal's own default-``deny`` approver for everything
else.
"""
import shal

TARGET_VOLTS = 3.3
TOLERANCE = 0.02  # 2% (DoD) — comfortably outside sim-dmm's own ~0.3% noise


def test_bench_output_is_3v3(rig, check):
    with shal.approver(shal.AutoApprove()):
        rig.psu.set_voltage(TARGET_VOLTS)
    check("vout", rig.dmm.measure_voltage(), "V",
          min=TARGET_VOLTS * (1 - TOLERANCE), max=TARGET_VOLTS * (1 + TOLERANCE))
