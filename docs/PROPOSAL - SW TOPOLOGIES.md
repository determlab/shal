# PROPOSAL — Pure-software topologies for SHAL (demo candidates)

Status: **proposal only, nothing implemented.** 2026-06-10.

Question under test: *is SHAL's model — "a bus is just a node that provides a
transport to its children" — genuinely general, or secretly hardware-shaped?*
The way to answer is to drive a **widely-used, real software topology** through
the unmodified Phase 1 core on one Windows PC.

## What a software topology must prove (success criteria)

1. The tree maps naturally — no node feels forced into the model.
2. At least one **multi-hop recursion** (bus stacked on bus) with `kinds()`
   validation doing real work.
3. The **retry/idempotency policy** is exercised by a real failure
   (kill a process mid-run): idempotent reads auto-recover with a WARNING;
   a write surfaces `delivered=unknown` and is NOT re-fired.
4. **Capabilities decouple**: the same user script runs against two different
   buses (the sim-parity trick already proven with the Deebot).
5. Zero changes to `shal/` — playground buses/drivers only.

## Wide survey — candidate software topologies

| # | Topology (real-world shape) | How wide-spread | Maps to SHAL as | PC effort | Phase 1 fit | Verdict |
|---|---|---|---|---|---|---|
| 1 | **Container estate** (Docker daemon → containers → processes inside) | The de-facto unit of software deployment | `docker` CLI = CommandTransport; `docker exec` = a *nested* CommandTransport per container | Docker Desktop (likely installed) | excellent — pure argv, the i2c-cli pattern verbatim | **PROPOSAL A** |
| 2 | **HTTP microservice mesh** (gateway → services → endpoints) | The dominant service architecture of the last 15 years | existing `shal,http` / `shal,tcp` buses, **zero new transport code** | none (stdlib servers) | excellent — uses two shipped kinds | **PROPOSAL B** |
| 3 | OS service fleet (Windows services / systemd units) | every ops/automation tool (Ansible's bread and butter) | `sc.exe`/`Get-Service` argv over `shal,local` | none | good, but single-hop — proves little recursion | runner-up: fold into A as a variant |
| 4 | WSL / VM estate (host → VM → guest processes) | very common on dev machines | `wsl.exe` as a CommandTransport hop — ssh-bus shape without ssh | WSL feature | good | fallback for A if no Docker |
| 5 | Kubernetes cluster (cluster → namespaces → pods) | the data-center standard | `kubectl` argv; namespaces map oddly (label, not hop) | kind/minikube install, heavy | medium | rejected: setup weight ≫ insight gained vs #1 |
| 6 | Message broker estate (MQTT/Kafka topics, consumers) | ubiquitous in IoT/eventing | needs the held-channel `Stream` kind | broker install | **poor — Phase 2** | rejected until streams exist |
| 7 | Database estate (server → schemas → tables) | universal | wire protocols too rich for `exchange()`; CLI (`sqlite3` argv) feels forced | none | medium | rejected: the tree adds nothing over a connection string |
| 8 | Cloud resources (account → region → VM/bucket) via az/aws CLI | universal in industry | argv over local; address grammar = resource ids | cloud account, credentials, cost | good on paper | rejected for a PC demo: external account + billing |
| 9 | Local AI stack (Ollama → models; OpenAI-compat endpoints) | rapidly becoming standard dev tooling | `shal,http` to localhost:11434; devices = models, capability `TextGenerator` | Ollama install | good | nice third demo someday; "widely used topology" claim is weaker |
| 10 | Smart-home hub (Home Assistant → integrations → entities) | huge installed base | HA REST over `shal,http` | HA in Docker | good | superseded by A (it IS a container demo) + we already did a cloud device |

## PROPOSAL A — "The container estate": Docker as a recursive bus tree

**The real-world topology it represents:** how software actually ships today —
a daemon supervising containers, software running inside them, operators
needing health/logs/exec/restart across the fleet. This is what `docker compose
ps`, portainer, and half of every SRE's day look like.

### Tree

```
/pc                      shal,local            (CommandTransport — exists)
  /docker                docker,cli            (NEW playground bus: renders docker
                                                argv onto upstream CommandTransport
                                                — exactly the i2c-cli pattern)
    /redis               docker,container      (device AND bus: provide_child_bus
                                                yields a docker-exec CommandTransport
                                                into THIS container — the mux pattern)
      /kv                redis,cli             (NEW driver: KeyValueStore capability
                                                via `redis-cli` argv INSIDE the container)
    /web                 docker,container
      /health            generic,http-health   (driver over... see note)
```

```yaml
shal_version: 1
root:
  pc:
    driver: shal,local
    address: localhost
    children:
      docker:
        driver: docker,cli          # NEW: ~80 lines, argv renderer
        address: default            # docker context name
        children:
          redis:
            id: cache
            driver: docker,container  # NEW: ~60 lines; device ops + exec bus
            address: shal-demo-redis  # container name = address grammar
            children:
              kv: { id: kv, driver: "redis,cli", address: "6379" }
```

### What it proves (mapped to the success criteria)

- **Three-deep argv recursion**: `redis-cli SET k v` → rendered by `redis,cli`
  → carried by the container's exec bus (`docker exec shal-demo-redis …`) →
  rendered by `docker,cli` → executed by `shal,local`. Every hop is the
  no-shell-strings contract earning its keep on pure software.
- **Dual-role nodes**: a container is a device (`restart()`, `is_running()`,
  `logs_tail()`) *and* a bus to its insides — the pca9548 `provide_child_bus`
  hook, reused unchanged.
- **Retry policy on real failures**: `docker stop` the redis container
  mid-script → idempotent `get()` raises delivered=no, reconnect/retry WARNING
  fires; `set()` during the kill surfaces `delivered=unknown` to the user.
  `restart()` is the audited write — the `shal.audit` paper trail demo.
- **Sim parity**: a `playground,sim-docker` bus answering the same argv shapes
  lets the identical demo script run with no Docker at all.

### Prereqs, effort, risks

- Docker Desktop running; `docker pull redis` (~30 MB). **Fallback variant:**
  if no Docker, the same proposal works over WSL (`wsl.exe -- …` as the exec
  hop) or degrades to survey row #3 (Windows services over `shal,local`).
- New playground code: 2 buses + 2 drivers ≈ 300 lines + YAML + demo script.
- Risk: container startup timing (mitigate: demo script waits on
  `is_running()`); Windows path quoting in argv (none — argv vectors, no shell).
- Honest limit: `logs --follow` / events need Phase 2 `Stream`; demo polls.

## PROPOSAL B — "The microservice mesh": HTTP/TCP services, zero new transports

**The real-world topology it represents:** a gateway fronting REST
microservices — the most widely deployed software architecture on earth.
Everything SHAL-side already ships: this is the *only* proposal that exercises
the codebase with **no new bus code at all**.

### Tree

```
/pc                      shal,local
  /api                   shal,http             (EXISTS — MessageTransport)
    /users               acme,user-service     (NEW driver: HealthCheck +
    /orders              acme,order-service     KeyValueStore capabilities)
  /worker                shal,tcp              (EXISTS — JSON-lines framing)
    /jobs                acme,job-runner       (NEW driver: JobRunner capability)
```

The services themselves: ~60 lines of Python **stdlib** (`http.server`,
`socketserver`) shipped in the playground — no pip installs, no Docker. Started
by the demo script, killed by it; one of them killed *deliberately mid-run*.

```yaml
shal_version: 1
root:
  api:
    driver: shal,http
    address: http://127.0.0.1:8001
    insecure: true                  # localhost demo; the loud opt-out on display
    children:
      users:  { id: users,  driver: "acme,user-service",  address: users }
      orders: { id: orders, driver: "acme,order-service", address: orders }
  worker:
    driver: shal,tcp
    address: 127.0.0.1:9001
    insecure: true
    children:
      jobs: { id: jobs, driver: "acme,job-runner", address: jobs }
```

### What it proves

- **The shipped MessageTransport stack against real sockets** — `shal,http`
  and `shal,tcp` have only ever been unit-tested; this is their first
  application-shaped outing. Connection caching on tcp (1 connect, N
  exchanges) becomes visible in the DEBUG log.
- **Retry policy with surgical precision**: the demo kills the worker between
  a request and its reply → `delivered=unknown` on a `submit_job()` write
  (never re-fired, user re-submits); restarts it → idempotent
  `health()`/`get_user()` ride the reconnect WARNING. The exact exactly-once
  story from DESIGN V2, on sockets you can see.
- **Capabilities across kinds**: `HealthCheck.ping()` implemented by an
  http-backed driver AND a tcp-backed driver — one script health-sweeps the
  whole estate via `isinstance(dev, HealthCheck)`, transport-blind.
- **Observability showcase**: run inside `shal.logging.capture()`; the
  flight-recorder JSON of a multi-service sweep with one failure is the
  "hand it to an AI" artifact, end to end.

### Prereqs, effort, risks

- Prereqs: **none** beyond Python. Lowest-friction demo possible.
- New playground code: 3 small drivers + 2 stdlib service stubs ≈ 250 lines.
- Risk: port collisions (pick high ports, fail loudly); Windows-firewall
  prompt on first listen (localhost only — usually silent).
- Honest limits: no real TLS on localhost (the `insecure: true` opt-out is
  itself part of the demo's story); no pub/sub (Phase 2); the `routes:`
  failover story (two replicas of one service!) parses but can't run — worth
  *showing* the YAML that will work in Phase 2.

## Recommendation

Run **B first** (an afternoon, zero installs, validates shipped buses), then
**A** (the deeper claim: recursion and dual-role nodes on the world's most
common software substrate). Together they cover all three transport kinds,
both failure modes of the retry policy, sim parity, audit, and the flight
recorder — with `shal/` untouched.

## What this research already says about the codebase

- Strong signs of generality: nothing in the survey needed a new *kind* —
  argv + messages cover rows 1–5 and 8–10 entirely.
- Real gaps it would expose (all known, all Phase 2): `Stream` (rows 6, A's
  log-follow), `routes:` failover (B's replica story), watchdog.
- One open design question worth deciding before A: should `docker,cli`
  declare `kind = CommandTransport` strictly (must sit on local/ssh/wsl), or
  also run standalone at root? Strict is more honest; the i2c-cli precedent
  says strict.
