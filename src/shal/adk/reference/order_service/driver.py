"""acme,order-service — a software node: an order service over HTTP.

A service is a node under the same rules as hardware. The driver never knows the
wire: under ``shal,http`` it talks to the real service, under ``shal,sim-msg`` to
the twin in ``sim.py``. Every op sends one request envelope (``method``, ``path``,
``json``) and reads ``reply["json"]``. The bus holds the credentials
(``config: {headers: ...}`` on the bus node); the driver never holds a secret.

The labels follow the software rules: ``get_order`` is a read (``none``, live or
raise); ``place_order`` is a ``write`` because ``cancel_order`` undoes it and it
touches only this service's own data; ``cancel_order`` cannot be undone by this
driver, so it is gated (``actuator``).
"""
from __future__ import annotations

from shal import registry
from shal.driver import Driver, idempotent, op
from shal.transport import MessageTransport


@registry.register
class OrderService(Driver):
    compatible = "acme,order-service"
    kind = MessageTransport
    llm_ready = True

    def _request(self, method: str, path: str, **extra) -> dict:
        # one HTTP request; a non-2xx answer is a HopError raised by the bus
        return self.bus.exchange(self.addr, {"method": method, "path": path, **extra})

    @idempotent  # a GET: safe to auto-retry across transient drops
    @op("Read one order now (item, qty, status). Call when you need an order's "
        "current state.", side_effect="none")
    def get_order(self, order_id: str) -> dict:
        return self._request("GET", f"orders/{order_id}")["json"]

    # never @idempotent: re-sending a lost POST may place a second order
    @op("Place an order for an item. Returns the new order id. Undo it with "
        "cancel_order.", side_effect="write",
        params={"qty": {"minimum": 1, "maximum": 100}})
    def place_order(self, item: str, qty: int) -> str:
        reply = self._request("POST", "orders", json={"item": item, "qty": qty})
        return reply["json"]["order_id"]

    @op("Cancel an order. This driver cannot undo it.", side_effect="actuator")
    def cancel_order(self, order_id: str) -> None:
        self._request("DELETE", f"orders/{order_id}")

    @classmethod
    def authoring_meta(cls) -> dict:  # shal.catalog() detail (issue #1)
        return {
            "address_schema": {"type": "string", "minLength": 1,
                               "description": "the service's path under the bus "
                                              "base URL", "examples": ["orders"]},
            "config_schema": {"type": "object", "properties": {},
                              "additionalProperties": False},
        }
