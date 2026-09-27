"""#220: when a driver's `bind()` raises inside `loader.load_tree`, what was
already bound is closed — in reverse order, exactly once — and the caller gets
the ORIGINAL exception object, unchanged. A close that itself raises during that
cleanup is logged and never replaces the original."""
from __future__ import annotations

import gc
import logging

import pytest

import shal
from shal.buses.sim import SimI2cBus
from shal.loader import load_tree


class _BindBoom(Exception):
    """A non-LoadError raised by driver code in bind()."""


@shal.register
class BindRaiser(shal.Driver):
    """A root driver whose bind() raises whatever `exc` holds."""
    compatible = "test,bind-cleanup-raiser"
    kind = None
    exc: BaseException | None = None

    def bind(self, node):
        raise BindRaiser.exc

    @shal.op("Read.", side_effect="none")
    def read(self) -> int:
        return 1


def _topo(third="test,bind-cleanup-raiser"):
    return {"shal_version": 1, "root": {
        "a": {"driver": "shal,sim-i2c", "address": "sim0",
              "children": {"s": {"driver": "shal,sim-sensor", "address": 0x48}}},
        "b": {"driver": "shal,sim-i2c", "address": "sim1"},
        "c": {"driver": third, "address": 1},
    }}


@pytest.fixture
def closes(monkeypatch):
    seen: list[str] = []
    real_close = SimI2cBus.close

    def counting_close(self):
        seen.append(self.host.path)
        return real_close(self)
    monkeypatch.setattr(SimI2cBus, "close", counting_close)
    return seen


@pytest.mark.parametrize("exc", [_BindBoom("driver code raised in bind"),
                                 shal.LoadError("bind refused the address")],
                         ids=["non-load-error", "load-error"])
def test_a_raising_third_bind_closes_the_first_two_once_and_re_raises(closes, exc):
    BindRaiser.exc = exc
    with pytest.raises(type(exc)) as ei:
        load_tree(_topo())
    assert ei.value is exc                           # the original object
    assert closes == ["/b", "/a"]                    # reverse order, once each
    del ei
    gc.collect()
    assert closes == ["/b", "/a"]                    # and never again


def test_a_close_that_raises_during_cleanup_does_not_mask_the_original(
        monkeypatch, caplog):
    BindRaiser.exc = _BindBoom("the original")
    closed: list[str] = []

    def close(self):
        closed.append(self.host.path)
        if self.host.path == "/b":
            raise RuntimeError("close failed too")
    monkeypatch.setattr(SimI2cBus, "close", close)
    with caplog.at_level(logging.WARNING, logger="shal.loader"):
        with pytest.raises(_BindBoom) as ei:
            load_tree(_topo())
    assert ei.value is BindRaiser.exc
    assert closed == ["/b", "/a"]                    # /a still closed after /b failed
    (rec,) = [r for r in caplog.records
              if getattr(r, "event", None) == "bind_cleanup_failed"]
    assert rec.path == "/b" and rec.levelno == logging.WARNING


def test_the_happy_path_closes_nothing_at_load(closes):
    roots, ids, _ = load_tree(_topo(third="shal,sim-i2c"))
    assert closes == []
    assert [r.path for r in roots] == ["/a", "/b", "/c"]


def test_through_shal_load_a_bind_failure_closes_once_no_hal_fill_close(
        closes, monkeypatch):
    """#217 interplay: load_tree raising means no Hal is built, so the Hal-fill
    cleanup never runs on top of load_tree's — each bus closes exactly once."""
    BindRaiser.exc = _BindBoom("bind raised under shal.load")
    fill_closes: list[str] = []
    real = shal.hal._close_subtree

    def spy(node, seen, *rest):
        fill_closes.append(node.path)
        return real(node, seen, *rest)
    monkeypatch.setattr(shal.hal, "_close_subtree", spy)
    with pytest.raises(_BindBoom) as ei:
        shal.load(_topo())
    assert ei.value is BindRaiser.exc
    assert closes == ["/b", "/a"]
    assert fill_closes == []
    del ei
    gc.collect()
    assert closes == ["/b", "/a"]


# ---- #229: a load refused AFTER load_tree succeeded closes the bound tree ------

@shal.register
class PolicyTamper(shal.Driver):
    """Binds fine, but changes the approval policy while binding: load_tree
    succeeds and `shal.load` then refuses the change."""
    compatible = "test,bind-cleanup-policy-tamper"
    kind = None

    def bind(self, node):
        super().bind(node)
        shal.set_approver(shal.AutoApprove())

    @shal.op("Read.", side_effect="none")
    def read(self) -> int:
        return 1


def test_a_policy_change_at_bind_refuses_the_load_and_closes_the_tree_once(
        closes, caplog):
    approver_before = shal.get_approver()
    with caplog.at_level(logging.INFO, logger="shal.audit"):
        with pytest.raises(shal.LoadError,
                           match="changed the approval policy while loading"):
            shal.load(_topo(third="test,bind-cleanup-policy-tamper"))
    assert shal.get_approver() is approver_before                  # restored
    (rec,) = [r for r in caplog.records
              if getattr(r, "outcome", None) == "policy-changed"]  # audited
    assert rec.changed == ["approver"]
    assert closes == ["/b", "/a"]                     # reverse order, once each
    gc.collect()
    assert closes == ["/b", "/a"]                     # and never again


def test_a_close_that_raises_after_a_refused_load_does_not_mask_the_load_error(
        monkeypatch, caplog):
    closed: list[str] = []

    def close(self):
        closed.append(self.host.path)
        if self.host.path == "/b":
            raise RuntimeError("close failed too")
    monkeypatch.setattr(SimI2cBus, "close", close)
    with caplog.at_level(logging.WARNING, logger="shal.loader"):
        with pytest.raises(shal.LoadError,
                           match="changed the approval policy while loading"):
            shal.load(_topo(third="test,bind-cleanup-policy-tamper"))
    assert closed == ["/b", "/a"]                    # /a still closed after /b failed
    (rec,) = [r for r in caplog.records
              if getattr(r, "event", None) == "load_cleanup_failed"]
    assert rec.path == "/b" and rec.levelno == logging.WARNING


def test_the_happy_path_through_shal_load_closes_at_close_only(closes):
    hal = shal.load(_topo(third="shal,sim-i2c"))
    assert closes == []
    hal.close()
    assert sorted(closes) == ["/a", "/b", "/c"]
    del hal
    gc.collect()
    assert sorted(closes) == ["/a", "/b", "/c"]


def test_a_child_close_that_raises_still_closes_its_siblings_and_parent(
        monkeypatch, caplog):
    """Round 2 (#229): cleanup catches per NODE — one failed close in a nested
    subtree leaks nothing, and the warning names the node that failed."""
    closed: list[str] = []

    def close(self):
        closed.append(self.host.path)
        if self.host.path == "/a/m1":
            raise RuntimeError("close failed at sim-secret-address")
    monkeypatch.setattr(SimI2cBus, "close", close)
    topo = _topo(third="test,bind-cleanup-policy-tamper")
    topo["root"]["a"]["children"] = {
        m: {"driver": "shal,sim-i2c", "address": f"sim-{m}"}
        for m in ("m1", "m2", "m3")}
    with caplog.at_level(logging.WARNING, logger="shal.loader"):
        with pytest.raises(shal.LoadError,
                           match="changed the approval policy while loading") as ei:
            shal.load(topo)
    assert type(ei.value) is shal.LoadError and ei.value.__context__ is None
    expected = ["/b", "/a/m1", "/a/m2", "/a/m3", "/a"]   # once each, leaf->root
    assert closed == expected
    (rec,) = [r for r in caplog.records
              if getattr(r, "event", None) == "load_cleanup_failed"]
    assert rec.path == "/a/m1" and rec.levelno == logging.WARNING
    assert "RuntimeError" in rec.getMessage()
    assert "sim-secret-address" not in rec.getMessage()   # type only
    del ei
    gc.collect()
    assert closed == expected                             # and never again
