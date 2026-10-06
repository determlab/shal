"""tests/test_sim_unplug_id.py — issue #417: an unknown `SHAL_SIM_UNPLUG`
(or `--unplug`) value used to match no node and unplug nothing, giving a
false "healthy" pass. Proven against the REAL virtual-bench sample
(`src/shal/samples/virtual-bench/bench.yaml`): `psu0` has id `psu`, `dmm0`
has id `dmm`.

The `run_bench` tests copy the sample folder into `tmp_path` first and run
there -- `run_bench.py` writes `records.db`/`records/` next to itself, and
this file must never leave that litter in the real, committed sample
folder (the exact bug test_samples.py's own file-listing tests exist to
catch)."""
from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

import shal
from shal.errors import HopError, LoadError

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = ROOT / "src" / "shal" / "samples" / "virtual-bench"
BENCH_YAML = SAMPLE_DIR / "bench.yaml"


def _load_run_bench(folder: Path):
    spec = importlib.util.spec_from_file_location(
        "run_bench_unplug_id", folder / "run_bench.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bench_copy(tmp_path):
    """A scratch copy of the real sample folder -- `run_bench.py` writes
    `records.db`/`records/` next to itself, which must never land in the
    committed sample folder itself."""
    dest = tmp_path / "virtual-bench"
    shutil.copytree(SAMPLE_DIR, dest)
    return dest


# --------------------------------------------------------------------------- #
# shal.load() itself: the unknown-id check
# --------------------------------------------------------------------------- #

def test_unknown_unplug_id_raises_load_error_naming_the_valid_ones(monkeypatch):
    monkeypatch.setenv("SHAL_SIM_UNPLUG", "nope")
    with pytest.raises(LoadError) as ei:
        shal.load(str(BENCH_YAML))
    message = str(ei.value)
    assert "nope" in message
    assert "dmm" in message
    assert "psu" in message


def test_path_form_unplug_id_matches_the_same_as_the_bare_id(monkeypatch):
    monkeypatch.setenv("SHAL_SIM_UNPLUG", "dmm0")  # the path segment, not the id "dmm"
    with shal.load(str(BENCH_YAML)) as hal:
        with pytest.raises(HopError) as ei:
            hal.get_device("dmm").measure_voltage()
        assert ei.value.delivered == "no"


def test_unset_or_empty_env_loads_fine(monkeypatch):
    monkeypatch.delenv("SHAL_SIM_UNPLUG", raising=False)
    with shal.load(str(BENCH_YAML)) as hal:
        hal.get_device("dmm").measure_voltage()

    monkeypatch.setenv("SHAL_SIM_UNPLUG", "")
    with shal.load(str(BENCH_YAML)) as hal:
        hal.get_device("dmm").measure_voltage()


# --------------------------------------------------------------------------- #
# run_bench.py: the fast-path validation before spawning pytest
# --------------------------------------------------------------------------- #

def test_run_bench_unknown_unplug_id_exits_cannot_run_with_valid_ids_listed(
        bench_copy, capsys):
    run_bench = _load_run_bench(bench_copy)
    code = run_bench.main(["--unplug", "nope"])
    assert code == run_bench.EXIT_CANNOT_RUN
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is False
    assert "nope" in summary["error"]
    assert "dmm" in summary["error"]
    assert "psu" in summary["error"]


def test_run_bench_path_form_unplug_id_actually_unplugs(bench_copy, capsys):
    """The real end-to-end run: pytest actually executes against a scratch
    copy of the real sample, with the DMM unplugged by its PATH name, not
    its id. pytest-shal isn't on PyPI (test_virtual_bench_demo.py's own
    docstring explains why) so it is not always in this dev venv -- an
    honest skip, not a FAIL, for a gap this test cannot close."""
    pytest.importorskip("pytest_shal")
    run_bench = _load_run_bench(bench_copy)
    code = run_bench.main(["--unplug", "dmm0"])
    assert code == run_bench.EXIT_UNREACHABLE
    summary = json.loads(capsys.readouterr().out)
    assert summary["verdict"] == "error"
    assert summary["cause"] == "transport"
