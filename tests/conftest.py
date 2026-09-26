"""Shared test fixtures.

The actuation gate (issue #14) defaults to deny-when-headless, which would block
every actuator op in the suite. Tests are a sanctioned auto-approve environment,
so install AutoApprove for all tests by default; test_approval.py overrides it
locally with `shal.approver(...)` to exercise the real policy behavior.

The suite's everyday devices (`ti,tmp102`, `microchip,mcp23017`, `rigol,dp832`)
are the ADK reference set. No device driver ships registered (D1, #149), so the
suite imports the three references and their sim twins here, the way an operator
names them with `--drivers`.
"""
import pytest

import shal
import shal.adk.reference.mcp23017.driver  # noqa: F401
import shal.adk.reference.mcp23017.sim  # noqa: F401
import shal.adk.reference.rigol_dp832.driver  # noqa: F401
import shal.adk.reference.rigol_dp832.sim  # noqa: F401
import shal.adk.reference.tmp102.driver  # noqa: F401
import shal.adk.reference.tmp102.sim  # noqa: F401


@pytest.fixture(autouse=True)
def auto_approve():
    with shal.approver(shal.AutoApprove()):
        yield
