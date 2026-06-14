"""Two tiny 'production' services, stdlib only — the workload for the mesh demo.

    python services.py http <port>   # users + orders REST service
    python services.py tcp  <port>   # job worker, JSON-lines framing

The worker understands a poison job ("crash") that kills the whole process
WITHOUT replying — the deliberately-induced failure the demo is built around.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from socketserver import StreamRequestHandler, ThreadingTCPServer

USERS = {
    1: {"name": "Ada Lovelace", "role": "admin"},
    2: {"name": "Grace Hopper", "role": "engineer"},
}

ORDERS: dict[str, dict] = {}
JOBS: dict[str, str] = {}
_order_ids = (f"ORD-{n:04d}" for n in itertools.count(1))
_job_ids = (f"JOB-{n:04d}" for n in itertools.count(1))


# ---- HTTP: users + orders ----------------------------------------------------

def handle_users(body: dict) -> dict:
    match body:
        case {"cmd": "ping"}:
            return {"ok": True, "service": "users"}
        case {"cmd": "get_user", "id": uid} if uid in USERS:
            return {"ok": True, "user": USERS[uid]}
        case {"cmd": "get_user", "id": uid}:
            return {"ok": False, "error": f"no user {uid}"}
        case _:
            return {"ok": False, "error": f"unknown cmd {body.get('cmd')!r}"}


def handle_orders(body: dict) -> dict:
    match body:
        case {"cmd": "ping"}:
            return {"ok": True, "service": "orders"}
        case {"cmd": "place_order", "item": item, "qty": qty}:
            oid = next(_order_ids)
            ORDERS[oid] = {"item": item, "qty": qty, "status": "placed"}
            return {"ok": True, "order_id": oid}
        case {"cmd": "get_order", "order_id": oid} if oid in ORDERS:
            return {"ok": True, "order": ORDERS[oid]}
        case {"cmd": "get_order", "order_id": oid}:
            return {"ok": False, "error": f"no order {oid}"}
        case _:
            return {"ok": False, "error": f"unknown cmd {body.get('cmd')!r}"}


API_ROUTES = {"users": handle_users, "orders": handle_orders}


class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # the demo prints its own story
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        route = API_ROUTES.get(self.path.strip("/"))
        out = (route(body) if route
               else {"ok": False, "error": f"unknown service {self.path!r}"})
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


# ---- TCP: job worker (shal,tcp JSON-lines envelope) ----------------------------

def handle_jobs(payload: dict) -> dict:
    match payload:
        case {"cmd": "ping"}:
            return {"ok": True, "service": "jobs"}
        case {"cmd": "submit", "job": "crash"}:
            os._exit(1)  # dies mid-request, reply never sent — on purpose
        case {"cmd": "submit"}:
            jid = next(_job_ids)
            JOBS[jid] = "done"
            return {"ok": True, "job_id": jid, "status": "queued"}
        case {"cmd": "status", "job_id": jid} if jid in JOBS:
            return {"ok": True, "status": JOBS[jid]}
        case {"cmd": "status", "job_id": jid}:
            return {"ok": False, "error": f"no job {jid}"}
        case _:
            return {"ok": False, "error": f"unknown cmd {payload.get('cmd')!r}"}


class WorkerHandler(StreamRequestHandler):
    def handle(self) -> None:
        for line in self.rfile:
            envelope = json.loads(line)
            out = handle_jobs(envelope.get("payload", {}))
            self.wfile.write(json.dumps(out).encode() + b"\n")
            self.wfile.flush()


def main() -> None:
    match sys.argv[1:]:
        case ["http", port]:
            ThreadingHTTPServer(("127.0.0.1", int(port)), ApiHandler).serve_forever()
        case ["tcp", port]:
            ThreadingTCPServer.allow_reuse_address = True
            ThreadingTCPServer(("127.0.0.1", int(port)), WorkerHandler).serve_forever()
        case _:
            sys.exit("usage: services.py http|tcp <port>")


if __name__ == "__main__":
    main()
