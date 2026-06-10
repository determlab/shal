# The Mesh Showcase (Proposal B, implemented)

A real microservice estate — REST services over HTTP, a job worker over raw
TCP — described in one SHAL YAML and driven through the **unmodified** core
buses. Self-narrating: run it and read the screen.

```
python demo_mesh.py
```

Prerequisites: **none** beyond Python and `pip install -e .` of this repo.
The two services are stdlib-only (`http.server`, `socketserver`), started and
stopped by the demo itself on `127.0.0.1:8765` and `:9876`.

## The seven acts

1. **Boot** — two real processes start; `mesh.yaml` loads with every node
   validated at load time (drivers resolved, kinds checked, addresses parsed).
2. **Health sweep** — one `isinstance(dev, HealthCheck)` loop pings services on
   two different transports. Capability-blind code, the core SHAL promise.
3. **Real work** — users/orders/jobs round-trips; the log shows ONE tcp
   `connect` for many `exchange`s (connection caching you didn't write).
4. **Disaster** — a poison job kills the worker *after receive, before reply*:
   - the write raises `HopError(delivered='unknown')` and is **never re-fired**;
   - an idempotent read retries once (WARNING), then fails with `delivered='no'`;
   - the worker restarts and the same device object just reconnects.
5. **Audit trail** — every write performed (including the failed one) is in the
   `shal.audit` channel with outcome/duration/txn; reads are not.
6. **Flight recorder** — a sweep re-run inside `shal.logging.capture()`
   produces `mesh_flight.jsonl`: the txn-correlated, machine-stable file you
   hand to a teammate or an AI when something breaks.
7. **Epilogue** — the leverage summary + the Phase 2 `routes:` failover teaser.

## Files

| File | Role |
|---|---|
| `services.py` | the workload: REST (users+orders) + TCP job worker, with the deliberate `crash` poison pill |
| `mesh_drivers.py` | `acme,user-service` / `acme,order-service` / `acme,job-runner` + the `HealthCheck` capability |
| `mesh.yaml` | the estate: `shal,http` and `shal,tcp` (core, unmodified) with `insecure: true` localhost opt-outs |
| `demo_mesh.py` | the narrated show |
| `mesh_flight.jsonl` | produced by Act 6 — open it, or feed it to an AI |

## What this proves about SHAL

The Phase 1 core carried a pure-software topology with zero new transport
code: same tree, same retry policy, same logging schema that drive an I2C
sensor or a cloud robot. The known limits it makes visible are the Phase 2
items: no push/streaming (`Stream`), and `routes:` failover parses but
refuses to run.
