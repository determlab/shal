"""Node — the tree. Topology is immutable after load -> lock-free lookups."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .driver import Driver
    from .transport import Transport


class Node:
    def __init__(
        self,
        name: str,
        *,
        address: Any = None,
        id: str | None = None,
        parent: Node | None = None,
    ) -> None:
        self.name = name
        self.address = address
        self.id = id
        self.parent = parent
        self.children: dict[str, Node] = {}
        self.driver: Driver | None = None
        self.spec: dict[str, Any] = {}  # schema-validated keys; set by the loader
        self.ref_target: Node | None = None  # $ref: name pointer, never routed through
        self.exposed_bus: Transport | None = None  # set when parent provides a
        # per-child bus (mux channels); otherwise parent's driver is the bus

    @property
    def description(self) -> str | None:
        """Optional instance context from the topology (issue #1) — blended into
        the agent tool description so deployments distinguish like devices."""
        return self.spec.get("description")

    @property
    def exposed(self) -> bool:
        """`expose: false` omits this node from the agent tool surface
        (tool_schemas/tool_catalog/call_tool); still usable from Python."""
        return self.spec.get("expose", True)

    @property
    def path(self) -> str:
        if self.parent is None:
            return "/" + self.name
        return self.parent.path + "/" + self.name

    @property
    def parent_bus(self) -> Transport | None:
        """Bus exposed by the parent node; None at root. One relation, used everywhere."""
        from .transport import Transport
        if self.parent is None:
            return None
        if self.parent.exposed_bus is not None:  # mux channel etc.
            return self.parent.exposed_bus
        d = self.parent.driver
        return d if isinstance(d, Transport) else None

    def walk(self, _seen: set[int] | None = None):
        """Depth-first walk. Every tree walk carries a visited-set guard."""
        seen = _seen if _seen is not None else set()
        if id(self) in seen:
            return
        seen.add(id(self))
        yield self
        for child in self.children.values():
            yield from child.walk(seen)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Node {self.path}>"
