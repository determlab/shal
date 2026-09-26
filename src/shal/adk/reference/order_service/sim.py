"""Sim twin of the order service, on the ``shal,sim-msg`` bus.

The model holds the orders as state and answers the same request envelopes the
real service does: ``POST orders``, ``GET orders/<id>``, ``DELETE orders/<id>``.
The bus has already checked and normalised the envelope, exactly as ``shal,http``
does, so the model reads ``msg["method"]`` and ``msg["path"]``. A plain body is a
``200``; a ``{"status": ..., "json" | "text": ...}`` reply sets the status, and a
non-2xx one is a HopError on the driver's side, as on the wire. Import this module
next to ``driver.py`` (``--drivers``) and a ``shal,sim-msg`` bus builds one per node.
"""
from collections.abc import Mapping

from shal.buses.sim_msg import msg_sim_model


@msg_sim_model("acme,order-service")
class OrderServiceModel:
    def __init__(self) -> None:
        self.orders: dict[str, dict] = {}
        self._next = 1

    def handle(self, msg: Mapping) -> Mapping:
        method, path = msg.get("method"), str(msg.get("path", ""))
        if method == "POST" and path == "orders":
            order_id = f"ORD-{self._next:04d}"
            self._next += 1
            body = msg.get("json") or {}
            self.orders[order_id] = {"id": order_id, "item": body.get("item"),
                                     "qty": body.get("qty"), "status": "placed"}
            return {"status": 201, "headers": {"Location": f"orders/{order_id}"},
                    "json": {"order_id": order_id}}
        head, _, order_id = path.partition("/")
        if head != "orders" or not order_id:
            return {"status": 404, "text": f"no resource {path!r}"}
        order = self.orders.get(order_id)
        if order is None:
            return {"status": 404, "text": f"no order {order_id}"}
        if method == "GET":
            return dict(order)
        if method == "DELETE":
            order["status"] = "cancelled"
            return {"status": 204, "text": ""}
        return {"status": 405, "text": f"{method} not allowed"}
