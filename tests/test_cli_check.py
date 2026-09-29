"""`shal check` — conformance from the command line (shal#148, ADK R6).

A thin CLI over `conformance.check_driver`: exit 0 clean, 1 when the report has
problems, 2 when the check could not run. `--json` prints the report on stdout.
"""
import json
import subprocess
import sys

import pytest

from shal import cli

# Local driver modules, imported by module:Class from the test's cwd. Neither class
# is decorated with @register — `shal check module:Class` must work unregistered.
_GOOD = """
from shal import Driver, idempotent, op

class GoodThing(Driver):
    compatible = "test,check-good"
    kind = None
    llm_ready = True

    @idempotent
    @op("Read the level now.", side_effect="none")
    def level(self) -> int:
        return 11
"""

# The same shape as test_conformance.py::test_missing_op_metadata_is_a_problem
# (_NoMeta): llm_ready NOT set and an op with no @op metadata — a problem
# check_driver reports today. (`@op("...")` without side_effect is NOT one: it is
# legal and infers "actuator", fail-closed — driver.py `inferred_side_effect`; it is
# a warning (#162, #194), see _UNLABELLED.)
_BAD = """
from shal import Driver

class BadThing(Driver):
    compatible = "test,check-bad"
    kind = None

    def do_thing(self) -> int:
        return 1
"""

# A local class that claims a compatible the package already ships.
_SHADOW = """
from shal import Driver, idempotent, op

class Fake(Driver):
    compatible = "shal,sim-sensor"
    kind = None
    llm_ready = True

    @idempotent
    @op("Read the level now.", side_effect="none")
    def level(self) -> int:
        return 11
"""

# One @op with no side_effect: legal and gated, so a warning, never a problem (#162).
_UNLABELLED = """
from shal import Driver, op

class UnlabelledThing(Driver):
    compatible = "test,check-unlabelled"
    kind = None
    llm_ready = True

    @op("Start the thing.")
    def start(self) -> str:
        return "started"
"""

# One @idempotent op; `{label}` is empty (no side_effect: "actuator", gated and
# audited like any unlabelled op — the same warning, #194) or a declared
# `, side_effect="..."`.
_IDEMPOTENT = """
from shal import Driver, idempotent, op

class SetpointThing(Driver):
    compatible = "test,check-idempotent"
    kind = None
    llm_ready = True

    @idempotent
    @op("Set the output to an absolute level."{label})
    def set_level(self) -> str:
        return "set"
"""


def _omission_warning(op: str) -> str:
    """The ONE text for any op with no side_effect, @idempotent or not (#194)."""
    return (f'{op}: no side_effect declared; treated as actuator (gated, audited). '
            f'Declare it — "none" for a read, "write" for a benign, reversible '
            f'change, "config"/"actuator" for a gated one.')

_YAML = ("shal_version: 1\n"
         "root:\n"
         "  dev: {id: dev, driver: 'test,check-good', address: a}\n")


def _shal(*argv: str, cwd=None) -> subprocess.CompletedProcess:
    """Run the real command in a fresh process (not an import of `main`)."""
    return subprocess.run([sys.executable, "-m", "shal.cli", *argv], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8", timeout=60)


@pytest.fixture
def drivers(tmp_path, monkeypatch):
    (tmp_path / "check_good_driver.py").write_text(_GOOD, encoding="utf-8")
    (tmp_path / "check_bad_driver.py").write_text(_BAD, encoding="utf-8")
    (tmp_path / "check_shadow_driver.py").write_text(_SHADOW, encoding="utf-8")
    (tmp_path / "check_unlabelled_driver.py").write_text(_UNLABELLED, encoding="utf-8")
    for mod, label in (("check_idem_bare_driver", ""),
                       ("check_idem_none_driver", ', side_effect="none"'),
                       ("check_idem_write_driver", ', side_effect="write"')):
        (tmp_path / f"{mod}.py").write_text(_IDEMPOTENT.format(label=label),
                                            encoding="utf-8")
    (tmp_path / "sim.yaml").write_text(_YAML, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))  # `check` puts cwd on it
    return tmp_path


def test_registered_compatible_json_is_valid_and_exits_0():
    r = _shal("check", "shal,sim-sensor", "--json")
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["compatible"] == "shal,sim-sensor"
    assert report["ok"] is True and report["problems"] == []
    assert report["checked"]


def test_a_real_check_driver_problem_exits_1_and_json_names_it(drivers):
    r = _shal("check", "check_bad_driver:BadThing", "--json", cwd=drivers)
    assert r.returncode == 1, r.stderr
    report = json.loads(r.stdout)
    assert report["ok"] is False
    assert any("must set llm_ready = True" in p for p in report["problems"]), report
    assert "Traceback" not in r.stderr


def test_an_unlabelled_op_is_a_json_warning_and_exits_0(drivers):
    r = _shal("check", "check_unlabelled_driver:UnlabelledThing", "--json",
              cwd=drivers)
    assert r.returncode == 0, r.stdout + r.stderr   # warnings never fail
    report = json.loads(r.stdout)
    assert report["ok"] is True and report["problems"] == []
    assert report["warnings"] == [_omission_warning("start")]


def test_an_idempotent_op_without_side_effect_gets_the_same_warning(drivers):
    # #194 folds #183's problem into the one omission warning: the runtime now
    # gates the op, so nothing is left ungated for the check to fail
    r = _shal("check", "check_idem_bare_driver:SetpointThing", "--json",
              cwd=drivers)
    assert r.returncode == 0, r.stdout + r.stderr   # warnings never fail
    report = json.loads(r.stdout)
    assert report["ok"] is True and report["problems"] == []
    assert report["warnings"] == [_omission_warning("set_level")]
    assert "Traceback" not in r.stderr


@pytest.mark.parametrize("mod", ["check_idem_none_driver", "check_idem_write_driver"])
def test_the_same_idempotent_op_with_side_effect_declared_is_clean(drivers, mod):
    r = _shal("check", f"{mod}:SetpointThing", "--json", cwd=drivers)
    assert r.returncode == 0, r.stdout + r.stderr
    report = json.loads(r.stdout)
    assert report["ok"] is True
    assert report["problems"] == [] and report["warnings"] == []


def test_unregistered_module_class_checks_clean(drivers, capsys):
    assert cli.main(["check", "check_good_driver:GoodThing", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report == {**report, "compatible": "test,check-good", "ok": True,
                      "problems": []}


def test_module_class_with_topology_runs_live_probes(drivers, capsys):
    assert cli.main(["check", "check_good_driver:GoodThing",
                     "--topology", "sim.yaml", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)["checked"]
    assert any(c.startswith("live:") for c in checked), checked


def test_text_report_without_json(drivers, capsys):
    assert cli.main(["check", "check_bad_driver:BadThing"]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM  device driver must set llm_ready = True" in out
    with pytest.raises(json.JSONDecodeError):
        json.loads(out)


@pytest.mark.parametrize("target, says", [
    ("nope,missing", "no driver installed for compatible 'nope,missing'"),
    ("no_such_module_148:X", "failed importing module 'no_such_module_148'"),
    ("json:Nope", "module 'json' has no attribute 'Nope'"),
    ("json:JSONDecoder", "is not a shal Driver subclass"),
    ("json:", "is not module:Class"),
])
def test_a_check_that_cannot_run_exits_2_on_stderr(target, says, capsys):
    assert cli.main(["check", target, "--json"]) == 2
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["ok"] is False and says in out["error"]
    assert says in captured.err


def test_module_class_never_shadows_a_shipped_compatible(drivers):
    # a fresh process: nothing has loaded the shipped registrations yet, which is
    # the state the guard must not be fooled by
    r = _shal("check", "check_shadow_driver:Fake", "--json", cwd=drivers)
    assert r.returncode == 2, r.stdout
    assert json.loads(r.stdout)["ok"] is False
    assert "compatible 'shal,sim-sensor' is already registered by" in r.stderr


def test_check_of_a_missing_driver_json_is_ok_false_exit_2(tmp_path):
    r = _shal("check", "nope,missing", "--json", cwd=tmp_path)
    assert r.returncode == 2
    assert json.loads(r.stdout)["ok"] is False


def test_missing_topology_exits_2(capsys):
    assert cli.main(["check", "shal,sim-sensor", "--topology", "no/such.yaml"]) == 2
    assert "topology file not found" in capsys.readouterr().err


def test_import_failure_is_a_message_not_a_traceback(drivers):
    (drivers / "check_broken_driver.py").write_text("raise RuntimeError('boom')\n",
                                                   encoding="utf-8")
    r = _shal("check", "check_broken_driver:X", cwd=drivers)
    assert r.returncode == 2
    assert "RuntimeError: boom" in r.stderr
    assert "Traceback" not in r.stderr


def test_help_shows_the_three_forms():
    r = _shal("check", "--help")
    assert r.returncode == 0
    for form in ("vendor,part", "module:Class", "--topology"):
        assert form in r.stdout
