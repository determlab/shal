"""The ADK reference set (#149, ADK §3.6 / R7): drivers shipped inside the
Authoring Kit as guide material — not registered, not imported by `import shal`,
absent from `catalog()` — each a driver, its sim twin, a test and a topology.
`sqlite` (#157, §3.7) is a root driver whose twin is its address (":memory:"),
so it has no sim.py. Other references land in parallel (#152): every check here
names the references it knows and never counts the folder.
"""
import json
import subprocess
import sys
from importlib.metadata import entry_points
from importlib.resources import files
from pathlib import Path

import pytest

from shal import cli
from shal.adk.reference.mcp23017.driver import Mcp23017
from shal.adk.reference.rigol_dp832.driver import RigolDp832
from shal.adk.reference.sqlite.driver import SqliteDatabase
from shal.adk.reference.tmp102.driver import Tmp102
from shal.conformance import check_driver

REFS = {"tmp102": Tmp102, "mcp23017": Mcp23017, "rigol_dp832": RigolDp832,
        "sqlite": SqliteDatabase}
#: references whose twin is the node address, not a sim.py (sqlite: ":memory:")
ADDRESS_TWIN = {"sqlite"}


def _ref_dir(name: str) -> Path:
    return Path(str(files("shal") / "adk" / "reference" / name))


@pytest.mark.parametrize("name", REFS)
def test_each_reference_is_a_triple_plus_topology(name):
    have = {p.name for p in _ref_dir(name).iterdir() if p.is_file()}
    assert {"driver.py", f"test_{name}.py", "topology.yaml"} <= have
    assert ("sim.py" in have) is (name not in ADDRESS_TWIN)


@pytest.mark.parametrize("name, cls", REFS.items())
def test_check_on_each_reference_is_clean(name, cls):
    # the bar in ADK §3.6: zero problems AND zero warnings, live probes included
    report = check_driver(cls, _ref_dir(name) / "topology.yaml")
    assert report.problems == []
    assert report.warnings == []
    assert any(c.startswith("live:") for c in report.checked), report.checked


@pytest.mark.parametrize("name", REFS)
def test_each_reference_test_file_passes_where_it_lives(name):
    # its own process: every reference imports modules literally named `driver`
    # and `sim`, as a copied reference will, so two must never share a process
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                        str(_ref_dir(name))], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


def test_import_shal_does_not_import_or_register_the_references():
    code = ("import json, sys, shal; c = shal.catalog(); "
            "print(json.dumps({'mods': [m for m in sys.modules if m.startswith('shal.adk')],"
            " 'ids': [e['compatible'] for e in c['drivers']]}))")
    out = json.loads(subprocess.run([sys.executable, "-c", code], capture_output=True,
                                    text=True, check=True).stdout)
    assert out["mods"] == []
    assert not {cls.compatible for cls in REFS.values()} & set(out["ids"])


def test_no_device_driver_entry_point():
    names = [ep.name for ep in entry_points(group="shal.drivers")
             if ep.value.startswith("shal.")]
    assert names and all(n.startswith("shal,") for n in names), names


def test_docs_list_names_each_reference(capsys):
    assert cli.main(["docs", "--list"]) == 0
    out = capsys.readouterr().out
    for name, cls in REFS.items():
        assert f"  {name} " in out and cls.compatible in out
    assert "shal docs --example" in out


def test_docs_example_prints_the_four_files(capsys):
    assert cli.main(["docs", "--example", "tmp102"]) == 0
    out = capsys.readouterr().out
    heads = [ln.split()[2] for ln in out.splitlines() if ln.startswith("# ==== ")]
    assert heads == ["driver.py", "sim.py", "test_tmp102.py", "topology.yaml"]
    assert (_ref_dir("tmp102") / "driver.py").read_text(encoding="utf-8").strip() in out
    assert "--drivers driver.py --drivers sim.py" in out


def test_docs_example_sqlite_prints_three_files_and_no_sim(capsys):
    # the twin is the address, so there is no sim.py to print or to name
    assert cli.main(["docs", "--example", "sqlite"]) == 0
    out = capsys.readouterr().out
    heads = [ln.split()[2] for ln in out.splitlines() if ln.startswith("# ==== ")]
    assert heads == ["driver.py", "test_sqlite.py", "topology.yaml"]
    assert "shal probe topology.yaml --drivers driver.py\n" in out
    assert "sim.py" not in out.split("# ==== ")[0]
    assert 'address: ":memory:"' in out


def test_docs_example_unknown_name_exits_2(capsys):
    assert cli.main(["docs", "--example", "nope"]) == 2
    err = capsys.readouterr().err
    assert "no reference named 'nope'" in err and "tmp102" in err
