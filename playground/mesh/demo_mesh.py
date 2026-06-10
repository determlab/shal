"""THE MESH SHOWCASE — a real microservice estate driven through SHAL.

    python demo_mesh.py

What you will watch, live:
  ACT 1  two real services boot; one YAML file becomes a device tree
  ACT 2  one loop health-checks services on TWO different transports
  ACT 3  real work — and the connection caching you didn't have to write
  ACT 4  we CRASH a service mid-request: the exactly-once story
  ACT 5  the audit trail that appeared while you weren't looking
  ACT 6  the flight recorder — the file you hand to an AI when it breaks

Everything indented and prefixed below is SHAL's own structured logging
(ConsoleFormatter). Everything else is this script narrating.
"""
from __future__ import annotations

import contextlib
import logging
import socket
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

# Windows consoles often default to cp1252 — the show uses box-drawing chars
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import mesh_drivers  # noqa: E402, F401  registers acme,* drivers
from mesh_drivers import HealthCheck  # noqa: E402

import shal  # noqa: E402

HTTP_PORT, TCP_PORT = 8765, 9876


# ---- presentation helpers ------------------------------------------------------

def act(n: int, title: str) -> None:
    print(flush=True)
    print("═" * 70)
    print(f"  ACT {n} — {title}")
    print("═" * 70, flush=True)


def say(*lines: str) -> None:
    for line in lines:
        print(f"  {line}")
    print(flush=True)


def leverage(*lines: str) -> None:
    print()
    for line in lines:
        print(f"  >>> LEVERAGE: {line}")
    print(flush=True)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(shal.logging.ConsoleFormatter(
        "      %(levelname)-7s %(name)-26s %(message)s"))
    logging.basicConfig(level=logging.WARNING, handlers=[handler])
    logging.getLogger("shal").setLevel(logging.DEBUG)     # the full hop story
    audit = logging.getLogger("shal.audit")               # silent by default —
    audit.addHandler(handler)                             # we opt in (one line)
    audit.setLevel(logging.INFO)


# ---- service process management --------------------------------------------------

PROCS: list[subprocess.Popen] = []


def _port_open(port: int) -> bool:
    with contextlib.suppress(OSError):
        socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
        return True
    return False


def start_service(kind: str, port: int) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, str(HERE / "services.py"), kind, str(port)])
    PROCS.append(p)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if _port_open(port):
            return p
        time.sleep(0.1)
    raise RuntimeError(f"{kind} service did not come up on :{port}")


@contextlib.contextmanager
def service_fleet():
    """Terminate every service we spawned — including mid-demo restarts —
    no matter how the show ends."""
    try:
        yield
    finally:
        for p in PROCS:
            if p.poll() is None:
                p.terminate()


# ---- the show ---------------------------------------------------------------------

def main() -> int:
    setup_logging()
    print(__doc__)

    act(1, "BOOT — one YAML file becomes a device tree")
    say("Starting two REAL processes (stdlib Python, no frameworks):",
        f"  - REST service (users + orders)   on http://127.0.0.1:{HTTP_PORT}",
        f"  - job worker (JSON over TCP)      on 127.0.0.1:{TCP_PORT}")
    with service_fleet():
        start_service("http", HTTP_PORT)
        start_service("tcp", TCP_PORT)
        say("Now loading mesh.yaml. Watch SHAL validate every node at LOAD time —",
            "drivers resolved, transport kinds checked, addresses validated:")
        with shal.load(HERE / "mesh.yaml") as hal:
            run_show(hal)
    return 0


def run_show(hal: shal.Hal) -> None:
    users = hal.get_device("users")
    orders = hal.get_device("orders")
    jobs = hal.get_device("jobs")

    leverage("nothing connected yet — SHAL is lazy. The tree is a MAP,",
             "connections open on first use, hop by hop.")

    # ----------------------------------------------------------------- ACT 2
    act(2, "HEALTH SWEEP — one loop, two different transports")
    say("The script asks for a CAPABILITY (HealthCheck), not a transport.",
        "'users' and 'orders' live behind HTTP; 'jobs' behind a raw TCP socket.",
        "Same line of code for all three:")
    for dev_id in ("users", "orders", "jobs"):
        dev = hal.get_device(dev_id)
        assert isinstance(dev, HealthCheck)
        print(f"  ping {dev_id:<8} -> {'UP' if dev.ping() else 'DOWN'}")
        print(flush=True)
    leverage("your code depends on WHAT a thing can do, never on HOW it is",
             "reached. Move 'jobs' behind HTTPS tomorrow: this loop won't change.")

    # ----------------------------------------------------------------- ACT 3
    act(3, "REAL WORK — and the plumbing you didn't have to write")
    say("A lookup, a write, a read-back — across both services:")
    user = users.get_user(1)
    print(f"  get_user(1)              -> {user['name']} ({user['role']})")
    order_id = orders.place_order(item="GPU", qty=2)
    print(f"  place_order('GPU', 2)    -> {order_id}")
    print(f"  get_order({order_id!r})  -> {orders.get_order(order_id)['status']}")
    print(flush=True)
    say("Now three job submissions over the TCP worker. Watch the log:",
        "ONE 'connect' event, THREE 'exchange' events — the connection is",
        "cached across calls, per bus, with correct locking. You wrote none of it:")
    job_ids = [jobs.submit_job("resize"), jobs.submit_job("encode"),
               jobs.submit_job("upload")]
    print(f"  submitted: {', '.join(job_ids)}")
    print(f"  status({job_ids[0]})    -> {jobs.job_status(job_ids[0])}")
    print(flush=True)
    leverage("every record above shares one txn id per call — grep a txn,",
             "get the whole multi-hop story. That's rule 6 of the logging design.")

    # ----------------------------------------------------------------- ACT 4
    act(4, "DISASTER — we crash the worker MID-REQUEST, on purpose")
    say("The next job is a poison pill: the worker process dies AFTER receiving",
        "the request but BEFORE replying. The request was delivered... probably?",
        "This is the exactly-once problem every distributed system has:")
    try:
        jobs.submit_job("crash")
    except shal.HopError as e:
        print("  RAISED: shal.HopError")
        print(f"          delivered = {e.delivered!r}")
        print(f"          {e}")
        print(flush=True)
    leverage("delivered='unknown' on a WRITE means SHAL WILL NOT re-fire it.",
             "A blind retry here could run the job twice — charge twice, move",
             "a motor twice. SHAL hands the decision to the only party that can",
             "make it safely: you. (DESIGN V2, locked decision 6.)")

    say("The worker is now DEAD. Watch an IDEMPOTENT read behave differently:",
        "SHAL retries it once (reconnect-and-retry WARNING below), then fails",
        "loudly with delivered='no' — it never reached the service at all.",
        "(The few seconds of silence you're about to feel are the TCP connect",
        "timing out — twice. That pause IS the retry policy, audibly at work.)")
    try:
        jobs.ping()
    except shal.HopError as e:
        print(f"  RAISED after 1 retry: delivered = {e.delivered!r}")
        print(flush=True)

    say("Ops restarts the worker (we do it here). NO code changes, NO bus",
        "rebuild — the next call just reconnects:")
    start_service("tcp", TCP_PORT)
    print(f"  ping jobs -> {'UP' if jobs.ping() else 'DOWN'}   (fresh socket, same device object)")
    print(flush=True)
    say(f"Note: {job_ids[0]} survived in our script, but the crash wiped the",
        "worker's memory — exactly why a delivery-unknown write must reach a",
        "human: only you know whether 'submit it again' is safe.")

    # ----------------------------------------------------------------- ACT 5
    act(5, "THE AUDIT TRAIL — it was recording the whole time")
    say("Scroll up: every 'shal.audit' line above is a WRITE we performed —",
        "place_order, submit_job — with outcome, duration and txn. The failed",
        "crash submission is there too, outcome=error, delivered=unknown.",
        "Reads (ping, get_user, job_status) are NOT in the audit channel.",
        "It's silent by default; we enabled it with ONE line in setup_logging().")
    leverage("'what did automation do to my fleet last night, and did it work?'",
             "is one grep. For actuators (motors, relays) swap 'order' for",
             "'movement' and this channel becomes a safety requirement.")

    # ----------------------------------------------------------------- ACT 6
    act(6, "THE FLIGHT RECORDER — debugging as a file you can hand over")
    flight = HERE / "mesh_flight.jsonl"
    say("Re-running a health sweep + one order inside shal.logging.capture().",
        "EVERYTHING (DEBUG hops, audit, timings) tees to JSON-lines, regardless",
        "of console verbosity:")
    with shal.logging.capture(flight):
        for dev_id in ("users", "orders", "jobs"):
            hal.get_device(dev_id).ping()
        hal.get_device("orders").place_order(item="SSD", qty=1)
    lines = flight.read_text(encoding="utf-8").splitlines()
    print(f"  wrote {len(lines)} structured records to {flight.name}; first two:")
    for line in lines[:2]:
        print(f"    {line[:120]}{'...' if len(line) > 120 else ''}")
    print(flush=True)
    leverage("when something breaks at 2 a.m., re-run inside capture() and hand",
             "the .jsonl to a teammate OR an AI assistant: stable event keys,",
             "txn-correlated, secrets redacted by construction. 'Why did this",
             "fail?' becomes answerable from ONE file.")

    # ----------------------------------------------------------------- EPILOGUE
    act(7, "EPILOGUE — what you just saw, in one breath")
    say("One YAML described an estate. One retry policy protected every write.",
        "One capability check swept two transports. One log schema told the",
        "whole story. ZERO lines of connection/retry/logging code in this file's",
        "business logic — and the same tree happily holds an I2C sensor next to",
        "your REST services.",
        "",
        "Phase 2 teaser — two replicas of the worker with ordered failover is",
        "already valid YAML (it parses; it refuses to run until implemented):",
        "",
        "    jobs:",
        "      id: jobs",
        "      driver: acme,job-runner",
        "      routes:",
        "        - { via: /worker_a, address: jobs }",
        "        - { via: /worker_b, address: jobs }")


if __name__ == "__main__":
    sys.exit(main())
