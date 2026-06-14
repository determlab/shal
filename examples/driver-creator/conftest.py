"""The driver-creator benchmark exercises generated drivers whose actuator ops
are gated by the human-in-the-loop approval interlock (issue #14). A benchmark
run is a sanctioned, non-interactive environment, so auto-approve every actuation
— the same posture the issue prescribes for sim/CI/tests.
"""
import pytest

import shal


@pytest.fixture(autouse=True)
def auto_approve():
    with shal.approver(shal.AutoApprove()):
        yield
