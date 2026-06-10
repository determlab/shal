"""SHAL simulation core: Node, Bus, Driver, loader, recursion.

This is a *validation harness*, not the real framework. Buses simulate IO so we
can test whether the recursive parent-bus `exchange` model holds across very
different bus worlds.
"""
import json

BUS_TYPES = {}
DRIVER_TYPES = {}


def bus(name):
    def deco(cls):
        cls.compatible = name
        BUS_TYPES[name] = cls
        return cls
    return deco


def driver(name):
    def deco(cls):
        cls.compatible = name
        DRIVER_TYPES[name] = cls
        return cls
    return deco


class Bus:
    remote = False        # True = crossing this bus puts children on the far side
    tunnelable = False    # True = can render an op to a string a remote bus carries
    supports_stream = False   # True = can hold a persistent channel for async events

    def __init__(self, host, cfg):
        self.host = host          # the node that PROVIDES this bus
        self.cfg = cfg
        self._active = False
        self.current_channel = None   # used by mux caching

    @property
    def upbus(self):
        return self.host.parent_bus   # the bus above my host (toward root)

    # ---- readiness ----
    def is_active(self):
        return self._active

    def activate(self):
        self._active = True

    def ensure_ready(self):
        if not self.is_active():
            self.activate()

    # ---- the one contract method ----
    def exchange(self, addr, payload):
        self.ensure_ready()
        if self.host.local_runtime:
            return self.perform(addr, payload)
        if not self.tunnelable:
            raise RuntimeError(
                f"{self.compatible}: host is remote (no agent) and this bus "
                f"can't render to a carryable command -> unreachable")
        raw = self.host.exchange(self.render(addr, payload))
        return self.parse(raw)

    # ---- async: held-channel subscription (mirror of exchange, reversed) ----
    def subscribe(self, addr, callback):
        if not self.supports_stream:
            raise RuntimeError(
                f"{self.compatible}: async needs a held-open channel, this bus "
                f"has none -> device cannot stream (weakest hop on the path)")
        if self.host.local_runtime:
            return self._open_stream(addr, callback)
        raise RuntimeError(
            f"{self.compatible}: remote held-stream not wired in this sim")

    def _open_stream(self, addr, callback):
        raise NotImplementedError(f"{self.compatible}._open_stream")

    # subclasses implement:
    def perform(self, addr, payload):
        raise NotImplementedError(f"{self.compatible}.perform")

    def render(self, addr, payload):
        raise NotImplementedError(f"{self.compatible}.render")

    def parse(self, raw):
        return raw


@bus("local")
class LocalBus(Bus):
    """Root backplane / passthrough. Never performs real IO; just carries."""
    tunnelable = True

    def render(self, addr, payload):
        return payload

    def parse(self, raw):
        return raw

    def perform(self, addr, payload):
        raise RuntimeError("LocalBus has no device to perform IO on")


class Subscription:
    def __init__(self, cancel_fn):
        self._cancel = cancel_fn
        self.active = True

    def cancel(self):
        if self.active:
            self._cancel()
            self.active = False


class Driver:
    def __init__(self, node):
        self.node = node

    def io(self, payload):
        return self.node.exchange(payload)


class Node:
    def __init__(self, name, addr=None, id=None):
        self.name = name
        self.addr = addr
        self.id = id
        self.parent = None
        self.parent_bus = None     # bus on my parent that reaches me
        self.bus = None            # bus I provide to my children
        self.driver = None
        self.children = {}
        self.local_runtime = False

    def exchange(self, payload):
        return self.parent_bus.exchange(self.addr, payload)

    def subscribe(self, callback):
        return self.parent_bus.subscribe(self.addr, callback)

    def __getattr__(self, name):
        drv = self.__dict__.get("driver")
        if drv is not None and hasattr(drv, name):
            return getattr(drv, name)
        raise AttributeError(name)

    def __repr__(self):
        role = []
        if self.driver: role.append(self.driver.compatible)
        if self.bus: role.append(f"bus:{self.bus.compatible}")
        return f"<Node {self.name} {' '.join(role)}>"


class SHAL:
    def __init__(self):
        self.root = None
        self._by_id = {}

    @classmethod
    def load(cls, path):
        with open(path) as f:
            spec = json.load(f)
        hal = cls()
        deferred_refs = []
        hal.root = hal._build(spec, "root", None, deferred_refs)
        for parent, cname, target_id in deferred_refs:
            tid = target_id.lstrip("$")
            if tid not in hal._by_id:
                raise KeyError(f"reference ${tid} not found")
            parent.children[cname] = hal._by_id[tid]   # link, no recursion
        return hal

    def _build(self, d, name, parent, deferred_refs):
        node = Node(name, addr=d.get("addr"), id=d.get("id"))
        node.parent = parent
        if parent is None:
            node.local_runtime = True
            node.bus = LocalBus(node, d)
        else:
            node.parent_bus = parent.bus
            node.local_runtime = parent.local_runtime and not parent.bus.remote
            btype = d.get("bus")
            if btype:
                node.bus = BUS_TYPES[btype](node, d)
        if d.get("id"):
            self._by_id[d["id"]] = node
        if d.get("driver"):
            node.driver = DRIVER_TYPES[d["driver"]](node)
        for cname, cd in d.get("children", {}).items():
            if "ref" in cd:
                deferred_refs.append((node, cname, cd["ref"]))
                continue
            child = self._build(cd, cname, node, deferred_refs)
            node.children[cname] = child
        return node

    def get_device(self, id=None, path=None):
        if id is not None:
            return self._by_id[id]
        cur = self.root
        for part in [p for p in path.split("/") if p]:
            cur = cur.children[part]
        return cur

    def tree(self, node=None, depth=0, seen=None):
        node = node or self.root
        seen = seen if seen is not None else set()
        loc = "local" if node.local_runtime else "REMOTE"
        line = "  " * depth + f"{node.name} [{loc}]"
        if node.bus: line += f" provides={node.bus.compatible}"
        if node.driver: line += f" driver={node.driver.compatible}"
        if node.id: line += f" id={node.id}"
        if id(node) in seen:                       # cycle back-edge: stop, don't recurse
            return line + "  -> (ref, already shown)"
        seen.add(id(node))
        out = [line]
        for cname, c in node.children.items():
            if id(c) in seen:
                out.append("  " * (depth + 1) + f"{cname} -> ref:{c.id or c.name}")
            else:
                out.append(self.tree(c, depth + 1, seen))
        return "\n".join(out)
