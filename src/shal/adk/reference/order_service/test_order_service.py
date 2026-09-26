"""Tests for the order-service reference driver, on the shal,sim-msg sim bus.

The twin in sim.py answers the same request envelopes the real service does, so
no service runs. Run from anywhere: this file puts its own folder on sys.path, so
`driver` and `sim` are the two files next to it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import driver  # noqa: E402,F401  (registers acme,order-service)
import sim  # noqa: E402,F401     (registers the sim model)

import shal  # noqa: E402
from shal.conformance import check_driver  # noqa: E402

_TOPO = os.path.join(os.path.dirname(__file__), "topology.yaml")


@pytest.fixture
def shop():
    """The loaded tree, the order-service device, and the sim bus under it."""
    with shal.load(_TOPO) as hal:
        yield hal, hal.get_device("orders"), hal.get_node("shop").driver


def test_place_then_get_reads_the_twin(shop):
    _, orders, bus = shop
    order_id = orders.place_order("widget", 3)
    assert orders.get_order(order_id) == {"id": order_id, "item": "widget",
                                          "qty": 3, "status": "placed"}
    assert bus.model_for("orders").orders[order_id]["qty"] == 3


def test_get_order_is_a_get_through_the_envelope(shop, monkeypatch):
    _, orders, bus = shop
    order_id = orders.place_order("widget", 1)
    sent = []
    real = bus.exchange

    def spy(addr, msg):
        sent.append(msg)
        return real(addr, msg)

    monkeypatch.setattr(bus, "exchange", spy)
    orders.get_order(order_id)
    assert sent == [{"method": "GET", "path": f"orders/{order_id}"}]


def test_an_unknown_order_is_a_hop_error_not_a_default(shop):
    _, orders, _ = shop
    with pytest.raises(shal.HopError, match="HTTP 404"):
        orders.get_order("ORD-9999")


@pytest.mark.parametrize("qty", [0, 101])
def test_qty_out_of_range_is_rejected_before_the_bus(shop, qty):
    _, orders, bus = shop
    with pytest.raises(shal.LimitError):
        orders.place_order("widget", qty)
    assert bus.model_for("orders").orders == {}      # nothing reached the service


def test_place_order_is_undone_by_cancel_order(shop):
    _, orders, _ = shop
    order_id = orders.place_order("widget", 2)
    with shal.approver(shal.AutoApprove()):          # cancel is gated: approve it
        orders.cancel_order(order_id)
    assert orders.get_order(order_id)["status"] == "cancelled"


def test_cancel_order_stops_at_the_gate(shop):
    _, orders, bus = shop
    order_id = orders.place_order("widget", 2)
    with shal.approver(shal.DenyAll()), pytest.raises(shal.ApprovalDenied):
        orders.cancel_order(order_id)
    assert bus.model_for("orders").orders[order_id]["status"] == "placed"


def test_delivered_unknown_propagates(shop):
    _, orders, bus = shop
    bus.fail_delivered_unknown = True
    with pytest.raises(shal.HopError) as exc:
        orders.place_order("widget", 1)
    assert exc.value.delivered == "unknown"


def test_check_driver_zero_problems_zero_warnings():
    report = check_driver(driver.OrderService, _TOPO)
    assert report.problems == []
    assert report.warnings == []
