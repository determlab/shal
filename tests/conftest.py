"""Shared test fixtures.

The actuation gate (issue #14) defaults to deny-when-headless, which would block
every actuator op in the suite. Tests are a sanctioned auto-approve environment,
so install AutoApprove for all tests by default; test_approval.py overrides it
locally with `shal.approver(...)` to exercise the real policy behavior.
"""
import pytest

import shal


@pytest.fixture(autouse=True)
def auto_approve():
    with shal.approver(shal.AutoApprove()):
        yield
