"""Tests for the python,dbm reference driver: a root driver wrapping a library.

Address "sim" is the twin (the real dbm.dumb in a throwaway folder), so there is
no sim.py. `write` is proven by undoing it with the driver's own other op; the
root shape by loading with no bus; the recipe by `shal check` passing clean.

Run from anywhere: this file puts its own folder on sys.path, so `driver` is the
file next to it. Copy the files, rename, and keep going.
"""
import gc
import json
import os
import shutil
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402  (registers python,dbm)

import shal  # noqa: E402
from shal import cli  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_DIR = os.path.dirname(__file__)
_TOPO = os.path.join(_DIR, "topology.yaml")


@pytest.fixture
def store():
    with shal.load(_TOPO) as hal:
        yield hal.get_device("store")


def test_a_root_driver_loads_with_no_bus_and_opens_nothing_at_bind():
    assert driver.DbmStore.kind is None
    with shal.load(_TOPO) as hal:
        dev = hal.get_device("store")
        assert dev.bus is None      # no parent bus, and the loader did not ask for one
        assert dev._db is None      # lazy: nothing opened until the first op
        assert dev.list_keys() == []
        assert dev._db is not None


def test_put_then_get(store):
    assert store.put("colour", "blue") is None      # a new key replaced nothing
    assert store.get("colour") == "blue"
    assert store.list_keys() == ["colour"]


def test_put_is_undone_by_put_of_the_old_value(store):
    store.put("colour", "blue")
    old = store.put("colour", "red")
    assert old == "blue" and store.get("colour") == "red"
    store.put("colour", old)
    assert store.get("colour") == "blue"


def test_a_new_key_is_undone_by_delete(store):
    assert store.put("colour", "blue") is None
    store.delete("colour")
    assert store.list_keys() == []


def test_delete_is_undone_by_put(store):
    store.put("colour", "blue")
    removed = store.delete("colour")
    assert removed == "blue" and store.list_keys() == []
    store.put("colour", removed)
    assert store.get("colour") == "blue"


def test_a_missing_key_raises_never_a_default(store):
    # the store answered "no such key": a shal.Error (the device said no), not a HopError
    for call in (store.get, store.delete):
        with pytest.raises(driver.NoSuchKey) as ei:
            call("nope")
        assert isinstance(ei.value, shal.Error) and not isinstance(ei.value, shal.HopError)


def test_a_real_path_keeps_its_data_across_loads():
    folder = tempfile.mkdtemp()  # not tmp_path: runs wherever it is copied
    topo = {"shal_version": 1, "root": {"store": {
        "id": "store", "driver": "python,dbm", "address": os.path.join(folder, "store")}}}
    hal = None
    try:
        with shal.load(topo) as hal:
            hal.get_device("store").put("colour", "blue")
            hal.get_device("store").put("colour", "red")  # an update, not just a new key
        with shal.load(topo) as hal:
            assert hal.get_device("store").get("colour") == "red"
        assert os.path.isfile(os.path.join(folder, "store.dat"))  # dbm.dumb's own file
    finally:
        del hal
        gc.collect()  # the driver goes, and dbm.dumb closes, before its folder does
        shutil.rmtree(folder, ignore_errors=True)


def test_the_labels():
    labels = {name: fn.__shal_op__["side_effect"]
              for name, fn in driver.DbmStore.capability_ops().items()}
    assert labels == {"get": "none", "list_keys": "none",
                      "put": "write", "delete": "write"}


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.DbmStore, _TOPO)
    assert report.problems == [] and report.warnings == []
    assert any(c.startswith("live:") for c in report.checked)


def test_shal_check_passes_as_an_operator_runs_it(monkeypatch, capsys):
    # the command a cold agent runs on its copy: `shal check` exits 0, clean
    monkeypatch.chdir(_DIR)
    assert cli.main(["check", "driver:DbmStore", "--topology", "topology.yaml",
                     "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True
    assert report["compatible"] == "python,dbm"
    assert report["problems"] == [] and report["warnings"] == []
