"""Drivers for the mesh demo — three services, two transports, one capability.

These run on the UNMODIFIED core buses (shal,http / shal,tcp). Each driver
declares `kind = MessageTransport` and never knows which wire carries it —
that's the whole point.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from shal import Driver, Error, MessageTransport, idempotent, op, register


@runtime_checkable
class HealthCheck(Protocol):
    """Capability: every well-behaved service answers a ping."""

    def ping(self) -> bool: ...


class ServiceError(Error):
    """Transport succeeded; the SERVICE said no. The retry machinery must
    never see this (delivery was certain)."""


class _MeshDriver(Driver):
    """Shared plumbing: every mesh service speaks {ok, ...} envelopes and
    answers a ping — so HealthCheck is implemented once, here."""

    kind = MessageTransport

    def _call(self, **msg) -> dict:
        resp = self.bus.exchange(self.addr, msg)
        if not resp.get("ok"):
            raise ServiceError(f"{self.addr}: {resp.get('error', 'service error')}")
        return resp

    @idempotent
    def ping(self) -> bool:
        return bool(self._call(cmd="ping").get("ok"))


@register
class UserService(_MeshDriver):
    compatible = "acme,user-service"

    @idempotent
    def get_user(self, user_id: int) -> dict:
        return self._call(cmd="get_user", id=user_id)["user"]


@register
class OrderService(_MeshDriver):
    compatible = "acme,order-service"

    @op("Place an order for an item (a benign service write).", side_effect="write")
    def place_order(self, item: str, qty: int) -> str:
        """A WRITE: re-firing this places a second order. Never auto-retried."""
        return self._call(cmd="place_order", item=item, qty=qty)["order_id"]

    @idempotent
    def get_order(self, order_id: str) -> dict:
        return self._call(cmd="get_order", order_id=order_id)["order"]


@register
class JobRunner(_MeshDriver):
    compatible = "acme,job-runner"

    @op("Submit a job to run (a benign service write).", side_effect="write")
    def submit_job(self, job: str) -> str:
        """A WRITE: a lost reply means the job MAY be running. User decides."""
        return self._call(cmd="submit", job=job)["job_id"]

    @idempotent
    def job_status(self, job_id: str) -> str:
        return self._call(cmd="status", job_id=job_id)["status"]
