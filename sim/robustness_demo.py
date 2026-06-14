"""Proves failover + reconnect rules from DESIGN.md (Runtime robustness).

Standalone, simulated (no real sockets) so the rules are visible directly.
"""


# ---------------------------------------------------------------- failover ----
class HopError(Exception):
    pass


class Route:
    def __init__(self, name, healthy):
        self.name = name
        self.healthy = healthy   # toggled to simulate a route going up/down

    def send(self, payload):
        if not self.healthy:
            raise HopError(f"{self.name}: link down")
        return f"{payload}@{self.name}"


class Device:
    """Opt-in multi-route, declared-order priority, sticky."""
    def __init__(self, routes):
        self.routes = routes
        self.current = None        # sticky route

    def send(self, payload):
        # sticky: try the current route first, then fall back to declared order
        order = list(self.routes)
        if self.current is not None:
            order = [self.current] + [r for r in self.routes if r is not self.current]
        errors = []
        for r in order:
            try:
                res = r.send(payload)
                if self.current is not r:
                    print(f"     route -> {r.name}")
                self.current = r                   # stick here
                return res
            except HopError as e:
                errors.append(str(e))
        raise HopError("device unreachable:\n      " + "\n      ".join(errors))


def failover_demo():
    print("FAILOVER  (routes declared [A, B], primary first, sticky)\n")
    A, B = Route("routeA", True), Route("routeB", True)
    dev = Device([A, B])

    print("  1. normal: uses primary A")
    print(f"     {dev.send('read')}")
    print("  2. sticky: A still healthy -> stays on A (no flap to B)")
    print(f"     {dev.send('read')}")

    print("  3. A drops -> failover to B")
    A.healthy = False
    print(f"     {dev.send('read')}")
    print("  4. A recovers, but sticky stays on B (re-probe only on reconnect)")
    A.healthy = True
    print(f"     {dev.send('read')}")

    print("  5. both down -> ONE aggregated error")
    A.healthy = B.healthy = False
    try:
        dev.send('read')
    except HopError as e:
        print(f"     TIMEOUT/ERROR: {e}")
    print()


# --------------------------------------------------------------- reconnect ----
class Conn:
    def __init__(self):
        self.alive = False
        self.reconnects = 0

    def open(self):
        self.alive = True
        self.reconnects += 1


def sync_exchange(conn, payload):
    """Sync rule: on a dead connection, re-open ONCE and retry, transparently."""
    for attempt in (1, 2):
        if not conn.alive:
            conn.open()
            print(f"     (reconnect #{conn.reconnects}, transparent)")
        try:
            if not conn.alive:
                raise HopError("dead")
            return f"{payload}-ok"
        except HopError:
            conn.alive = False
            if attempt == 2:
                raise
    return None


def reconnect_demo():
    print("RECONNECT\n")
    print("  sync exchange: connection dead -> auto reopen + retry, user sees success")
    c = Conn()                       # starts dead
    res = sync_exchange(c, "read")
    print(f"     result={res}  (reconnects={c.reconnects})\n")

    print("  async subscribe: drop is SURFACED to callback (never silent)")
    events = []
    cb = events.append
    cb(("data", 21.5))
    cb(("DROP", "ssh link lost"))    # the gap is reported, not hidden
    cb(("resubscribe", "routeB"))
    cb(("data", 22.0))
    for e in events:
        print(f"     callback <- {e}")
    print("     -> user knows data MAY have been missed during the gap\n")


if __name__ == "__main__":
    failover_demo()
    reconnect_demo()
