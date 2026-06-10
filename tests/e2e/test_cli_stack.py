"""End-to-end: a device driver renders argv, the shal,local bus runs it as a
REAL subprocess, output is parsed back. The cross-platform analog of the
canonical tmp102 -> i2c-cli -> argv -> exec stack, exercised from a YAML file on
disk through load, bind, kinds() validation, the retry policy, and error mapping.

Driver + tool + fixtures live in conftest.py.
"""
import logging

import pytest

import shal


def test_read_through_real_subprocess(cli_rig):
    setup, _ = cli_rig
    with shal.load(setup) as hal:
        assert hal.get_device("thermo").read_celsius() == pytest.approx(25.0)


def test_idempotent_read_recovers_from_a_transient_drop(cli_rig, faults, caplog):
    setup, tmp_path = cli_rig
    with shal.load(setup) as hal:
        dev = hal.get_device("thermo")
        faults(tmp_path, 1)                      # next call fails, then recovers
        with caplog.at_level(logging.WARNING, logger="shal"):
            assert dev.read_celsius() == pytest.approx(25.0)
        assert any(getattr(r, "event", "") == "retry" for r in caplog.records)


def test_retry_is_once_not_a_loop(cli_rig, faults):
    setup, tmp_path = cli_rig
    with shal.load(setup) as hal:
        dev = hal.get_device("thermo")
        faults(tmp_path, 2)                      # both the call and its one retry fail
        with pytest.raises(shal.HopError):
            dev.read_celsius()


def test_write_is_argv_not_shell_and_not_retried(cli_rig, faults):
    setup, tmp_path = cli_rig
    with shal.load(setup) as hal:
        dev = hal.get_device("thermo")
        # a value full of shell metacharacters must reach the tool as inert data
        payload = "$(touch pwned); 9 && rm -rf /"
        assert dev.set_threshold(payload) == "ok"
        recorded = (tmp_path / "last_argv.txt").read_text(encoding="utf-8")
        assert payload in recorded                       # delivered literally
        assert not (tmp_path / "pwned").exists()         # never interpreted

        faults(tmp_path, 1)                              # a write that fails after send
        with pytest.raises(shal.HopError) as ei:
            dev.set_threshold("42")
        assert ei.value.delivered == "unknown"           # NOT auto-retried
