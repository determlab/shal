"""Route set (#235, routes M1 part 2): one routed node talks through one route at a time.

A node with ``routes:`` binds a ``RouteSet`` in place of its parent bus. It is a
``Transport`` of the driver's kind; its one kind method delegates to the ACTIVE
route's bus and puts that route's address in place of the driver's ``self.addr``,
so the driver never sees two addresses.

Invariants (RFC-001 §2-§3; DESIGN V2 "Routing"):

* One route at a time: the set has its own lock; callers queue there, then on
  the route's bus lock as today.
* Sticky: the active route is the first that delivers and stays active until it
  fails. ``close()`` / ``ensure_ready()`` go back to the main route (the first).
* ``delivered="no"`` (3a): nothing reached the device, so ANY op revives the same
  route once (close + ensure_ready), then moves to the next route.
* ``delivered="unknown"`` (3b): an ``@idempotent`` op retries once on the same
  route, then may move. Any other op STOPS — no retry, no move: a ``write`` /
  ``config`` / ``actuator`` op is never re-fired on another route.
* All routes down (3e): ONE ``HopError`` listing each route and its reason.
* A pinned call (``via="<name>"``, 3i) never moves; a failure names the route.
* The route set sits BELOW the op wrapper: limits and approval ran once, before
  any I/O, and no route change asks again.
"""
from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, NamedTuple

from .errors import Error, HopError
from .log import current_txn
from .transport import (
    ByteTransport,
    CommandTransport,
    Completed,
    MessageTransport,
    Op,
    Stream,
    Transport,
)

if TYPE_CHECKING:
    from .node import Node

logger = logging.getLogger("shal.route")


class RouteCall(NamedTuple):
    """What the op wrapper tells the route set about the op running now."""

    route_set: RouteSet   # the set this applies to (a nested device has its own)
    idempotent: bool      # may retry / move after delivered="unknown"
    via: str | None       # pinned route name, or None


# set by the op wrapper around the driver body; a transport call made outside an
# op (or through another route set) gets the safe default: not idempotent, no pin
current_call: ContextVar[RouteCall | None] = ContextVar("shal_route_call", default=None)

# the fix line of an all-routes-down error (3e)
_ALL_DOWN_FIX = ("check each route's link and bus; pin one with via=<name> "
                 "to try it alone")


class RouteSet(Transport):
    """The ordered routes of one node: ``[(name, bus node, address), ...]``, the
    main route first. Built by :func:`route_set_for`, which mixes in the kind."""

    def __init__(self, host: Node, routes: Sequence[tuple[str, Node, Any]]) -> None:
        super().__init__(host)
        self.routes: list[tuple[str, Node, Any]] = list(routes)
        self._current = 0  # index of the active route

    @property
    def names(self) -> list[str]:
        return [name for name, _, _ in self.routes]

    @property
    def active(self) -> str:
        """The name of the route the next unpinned call tries first."""
        return self.routes[self._current][0]

    def check_via(self, via: str) -> None:
        """Refuse an unknown route name, listing the valid ones. Pre-I/O."""
        if via not in self.names:
            raise Error(f"{self.host.path}: no route named {via!r}; "
                        f"routes: {', '.join(self.names)}")

    # lifecycle: the main route is tried again on the next close/ensure_ready cycle
    def activate(self) -> None:
        self._current = 0
        super().activate()

    def close(self) -> None:
        # the route buses are shared with their other children: not closed here
        with self.lock:
            self._current = 0
            super().close()

    # -- the one delegation path, for every kind -----------------------------------
    def _through(self, call: Callable[[Transport, Any], Any]) -> Any:
        ctx = current_call.get()
        if ctx is None or ctx.route_set is not self:
            ctx = RouteCall(self, False, None)
        with self.lock:
            self.ensure_ready()
            n = len(self.routes)
            if ctx.via is not None:
                self.check_via(ctx.via)
                order = [self.names.index(ctx.via)]
            else:
                order = [(self._current + k) % n for k in range(n)]
            failures: list[tuple[str, Node, HopError]] = []
            for i in order:
                name, bus_node, addr = self.routes[i]
                try:
                    result = self._on_route(name, bus_node, addr, call, ctx.idempotent)
                except HopError as e:
                    e.with_via(name)
                    if ctx.via is not None:
                        raise  # pinned: never moves (3i)
                    if e.delivered != "no" and not ctx.idempotent:
                        raise  # 3b: delivery unknown on a changing op — stop
                    failures.append((name, bus_node, e))
                    if len(failures) < n:
                        logger.warning("%s: route %s failed (delivered=%s); moving on",
                                       self.host.path, name, e.delivered,
                                       extra={"event": "failover",
                                              "path": self.host.path})
                    continue
                if ctx.via is None:
                    self._current = i  # sticky: the first route that delivers
                return result
            raise self._all_down(failures)

    def _on_route(self, name: str, bus_node: Node, addr: Any,
                  call: Callable[[Transport, Any], Any], idempotent: bool) -> Any:
        """One route: try, and on a retryable failure revive it once and retry."""
        bus = bus_node.bus
        if bus is None:  # _check_jumps refused this at load; kept loud
            raise HopError("route has no bus", path=bus_node.path, hop="route",
                           txn=current_txn.get())
        try:
            return call(bus, addr)
        except HopError as e:
            if e.delivered != "no" and not idempotent:
                raise  # 3b: no retry
            logger.warning("%s: route %s failed (delivered=%s); revive and retry (1/1)",
                           self.host.path, name, e.delivered,
                           extra={"event": "retry", "path": self.host.path})
        bus.close()
        bus.ensure_ready()
        return call(bus, addr)

    def _all_down(self, failures: list[tuple[str, Node, HopError]]) -> HopError:
        reasons = "; ".join(f"{name} via {bus_node.path}: {e._msg} "
                            f"(delivered={e.delivered})"
                            for name, bus_node, e in failures)
        delivered = ("unknown" if any(e.delivered != "no" for *_, e in failures)
                     else "no")
        last = failures[-1][2]
        return HopError(f"all {len(failures)} routes failed: {reasons}; {_ALL_DOWN_FIX}",
                        path=self.host.path, hop=last.hop, txn=current_txn.get(),
                        delivered=delivered, via=failures[-1][0])

    # -- the kind methods (only the mixed-in kinds are offered: kinds()) -----------
    def _own(self, addr: Any) -> None:
        # the driver passes its own address; the route set swaps in the route's
        if addr != self.host.address:
            raise Error(f"{self.host.path}: a routed node talks only to its own "
                        f"address (each route carries it); got another address")

    def txn(self, addr: Any, ops: Sequence[Op]) -> bytes:
        self._own(addr)
        return self._through(lambda bus, a: bus.txn(a, ops))

    def run(self, argv: Sequence[str], stdin: bytes = b"") -> Completed:
        return self._through(lambda bus, a: bus.run(argv, stdin))

    def exchange(self, addr: Any, msg: Mapping) -> Mapping:
        self._own(addr)
        return self._through(lambda bus, a: bus.exchange(a, msg))

    def subscribe(self, addr: Any, topic: str):
        self._own(addr)
        return self._through(lambda bus, a: bus.subscribe(a, topic))

    def validate_address(self, addr: Any) -> None:
        pass  # every route's address was checked against its bus at load


_KINDS = (ByteTransport, CommandTransport, MessageTransport, Stream)
_classes: dict[frozenset[type], type[RouteSet]] = {}


def route_set_for(node: Node, kind: type | None) -> RouteSet:
    """The ``RouteSet`` for a routed node, offering the driver's ``kind`` (or, for
    a driver that declares none, what its main bus offers)."""
    main = node.parent_bus
    kinds = frozenset({kind}) if kind is not None else (
        main.kinds() if main is not None else frozenset())
    cls = _classes.get(kinds)
    if cls is None:
        mixins = tuple(k for k in _KINDS if k in kinds)
        cls = _classes[kinds] = type(
            "RouteSet[" + ",".join(k.__name__ for k in mixins) + "]",
            (RouteSet, *mixins), {})
    return cls(node, node.routes)
