"""Proves the timeout rule: per-hop limit AND overall budget, layered.

effective hop limit = min(hop_timeout, remaining_budget); whichever fires first wins.
Budget shrinks as the call descends a hop can't exceed what's left.

(Standalone: uses simulated hop durations instead of real blocking, so the rule is
visible without wall-clock waits.)
"""


class Timeout(Exception):
    pass


class Hop:
    def __init__(self, name, delay, timeout):
        self.name = name        # which bus
        self.delay = delay      # simulated time this hop takes
        self.timeout = timeout  # this hop's own limit


def call(path, overall):
    """Walk root->leaf, enforcing both limits. Returns elapsed or raises Timeout."""
    elapsed = 0.0
    for hop in path:                       # root-side hop first, then inward
        remaining = overall - elapsed
        effective = min(hop.timeout, remaining)
        if hop.delay > hop.timeout:
            raise Timeout(f"{hop.name} hop ({hop.timeout}s) "
                          f"[delay {hop.delay}s > hop limit]")
        if hop.delay > remaining:
            raise Timeout(f"overall budget ({overall}s) "
                          f"[at {hop.name}: {hop.delay}s > {remaining:.2f}s left]")
        elapsed += hop.delay
        print(f"    {hop.name:4} ok  delay={hop.delay}s  eff=min({hop.timeout},"
              f"{remaining:.2f})={effective:.2f}  elapsed={elapsed:.2f}")
    return elapsed


def scenario(title, path, overall):
    print(f"  {title}  (overall budget {overall}s)")
    try:
        used = call(path, overall)
        print(f"    -> OK, total {used:.2f}s\n")
    except Timeout as e:
        print(f"    -> TIMEOUT: {e}\n")


print("Path: root -> ssh -> i2c -> leaf\n")

# 1) success: both hops fit, total under budget
scenario("1. fits",
         [Hop("ssh", 0.8, 1.0), Hop("i2c", 0.3, 0.5)], overall=2.0)

# 2) per-hop fires: i2c takes longer than its own 0.5s limit
scenario("2. hop limit fires",
         [Hop("ssh", 0.8, 1.0), Hop("i2c", 0.7, 0.5)], overall=2.0)

# 3) overall fires: each hop is within its own limit, but budget runs out
#    ssh uses 0.8 of a 1.0s budget -> only 0.2s left; i2c (0.3s, limit 0.5s) can't fit
scenario("3. overall budget fires",
         [Hop("ssh", 0.8, 1.0), Hop("i2c", 0.3, 0.5)], overall=1.0)
