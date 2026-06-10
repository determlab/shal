# SHAL — Phase 2 Async Design (streaming + watchdog)

**Status:** design only — not implemented. **Scope:** streaming core + watchdog/safe-state.
**Out of scope (forward seams only):** stream failover (`routes:` for subscriptions),
the agent-bus wire protocol, hotplug/discovery.

This document takes the **already-locked** async/streaming and actuator-safety decisions
in `DESIGN V2.md` ("Async / streaming", "Actuator safety", "Concurrency", "Lifecycle",
"Runtime robustness", "Logging & observability") and turns them into an implementation-grade
spec that fits the **Phase 1 sync core exactly** (`src/shal/transport.py`, `driver.py`,
`hal.py`, `node.py`, `errors.py`, `log.py`, `buses/mux.py`, `buses/ssh.py`, `buses/sim.py`).
It is purely **additive**: Phase 1 sync topologies and code keep working untouched.

> **Locked decisions are not revisited here.** Where this doc decides something, it decides
> *within* the locked rules and says why. The numbered "Locked rule N" references point at the
> Async section of `DESIGN V2.md`.

---

## 0. The one idea, and what it forces

Sync is **leaf→root request/response**, lazy and stateless: a capability call descends through
`upstream`, the answer unwinds, and nothing is held between calls. A **stream is the mirror**:
data originates **unsolicited at a leaf** and travels **root-ward**, each hop forwarding into
its parent's *held* channel until it reaches the user's reader. The recursion direction is the
same (everything funnels through `upstream`); the data flows the other way and **the channel
persists for the subscription's life**. That single difference — *held* vs *lazy* — is what
every contract below protects.

Consequences, all locked:
- Async is the **second primitive**: explicitly stateful, opt-in. Sync stays exactly as-is and
  keeps working on the same path (Rule 1).
- **Every hop must hold a stream**; the weakest hop decides; failure is **loud at setup**, never
  at 2 a.m. (Rule 1).
- Drops are **surfaced as `shal.Gap`, never hidden** (Rule 4).
- The user API is **sync-first**: `for ev in dev.events(...)` is a blocking iterator; `async for
  ev in dev.stream(...)` is the asyncio mirror; `sub = dev.subscribe(cb)` is the primitive both
  are built on.
- **One reader per subscription**; user code never blocks dispatch.
- The **watchdog** bounds what a *live* link leaves an actuator doing; it is honest about a dead
  link (it cannot reach through a severed link, and says so).

---

## 1. Canonical types & ownership (NORMATIVE)

Every later section references these. They are defined **once, here**. New runtime types live in
a new module `src/shal/stream.py`; `Gap` stays in `errors.py` (already shipped, widened
additively); `Transport`/kind-mixins stay stateless in `transport.py`.

### 1.1 The layering rule (resolves "what does subscribe return")

There are **two** layers, and they must not be conflated:

- **Transport layer** — `Stream.subscribe(addr, topic, *, sink, setup_timeout, idle_timeout)
  -> ChannelHandle`. This is the **per-hop plumbing** method (the thing already stubbed in
  `transport.py`). Each hop opens *its* held resource and returns a low-level `ChannelHandle`.
  A forwarding hop (mux) opens its parent's channel and wraps/pins.
- **Framework layer** — the framework constructs the **one** user-facing `Subscription`, which
  owns the **one** reader for the whole stream. `Subscription` is **not** returned by any hop's
  `subscribe`; it is built by the device-level helper (`Driver.subscribe`).

> This is the only model that fits Phase 1: hops are plumbing that return handles; the device
> owns the single reader (Locked: one reader per subscription).

### 1.2 `Event` — a delivered datum

```text
@dataclass(frozen=True)
class Event:
    kind: str            # driver-defined event kind ("pose", "dustbin_full", ...)
    payload: Any         # opaque to the framework (bytes | Mapping | decoded value)
    ts: float            # time.monotonic() stamped AT THE LOCAL READER on ingest
    seq: int             # per-subscription monotonic counter (framework-assigned)
    source_ts: float|None # OPTIONAL opaque leaf timestamp; framework NEVER compares it
```

`ts` is stamped at the **local reader**, not at the remote leaf — a leaf and the root can be
different machines (ssh `tail -f`); `time.monotonic()` is per-process and not cross-machine
comparable. Stamping locally makes `Event.ts`, `Gap.since/until`, and the watchdog clock all
share **one** monotonic timeline. Any leaf-provided timestamp rides as opaque `source_ts`.

### 1.3 `Gap` — a surfaced loss (widened additively)

`Gap` already exists (`errors.py`, `Gap(reason='')`). Phase 2 **adds optional, defaulted**
fields so `Gap()` and `Gap('drop')` still construct (backward compatible):

```text
class Gap:                       # an EVENT, never raised
    reason: str = ''             # 'drop' | 'overflow' | 'idle' | 'reconnecting' | ...
    hop: str|None = None         # which hop observed the loss
    path: str|None = None
    since: float|None = None     # monotonic ts of last delivered event (None = unknown lower bound)
    until: float|None = None     # None when emitted at detection; span closed via sub.last_gap (immutable)
    attempt: int = 0             # reconnect attempt index, if applicable
    dropped: int|None = None     # count if known (overflow); None for wire drops (honest: ts span, not count)
    local: bool = False          # True = local loss (slow consumer/overflow); False = wire/remote
```

Span semantics (resolves the immutability conflict): a `Gap` is **emitted immediately on
detection** with `until=None`, and is **never mutated after delivery**. The span is *closed* out
of band on `Subscription.last_gap` (an immutable replacement the consumer may poll), not by
editing a `Gap` already handed to a `for ev in ...` consumer. A coalescing overflow `Gap` is
**not enqueued until its window closes** (next real event / resume) — "learn now" is satisfied
by the subscription's `DEGRADED` state + the eventual immutable `Gap`, never by mutating a
delivered object.

`seq_lo`/`seq_hi` are a **forward seam** (some future buses have sequence numbers; per-stream
buses like `tail -f` do not, so SHAL must not claim to know *how many* events were missed).

### 1.4 `WatchdogTrip` — a surfaced safety event

A watchdog trip is **distinct** from a data Gap (data-loss ≠ device-safed). It is a marker
event, never an exception:

```text
@dataclass(frozen=True)
class WatchdogTrip:
    node_path: str
    cause: str               # 'silence' | 'connection_loss' | 'hop_failure'
    reachable: bool          # could safe_state() be DELIVERED?
    safe_state_ok: bool|None # True if safe_state() returned cleanly; None if unreachable
    ts: float                # monotonic
```

The iterator/stream element type is therefore **`Event | Gap | WatchdogTrip`**. Consumers branch
with `isinstance`; the canonical pattern stays `if isinstance(ev, shal.Gap): continue` and adds
`if isinstance(ev, shal.WatchdogTrip): ...`. (Alternative considered: reuse `Gap(reason=
'watchdog')` — rejected because it conflates two different facts; see Decision Log.)

### 1.5 `SubState` — the one subscription state machine

```text
OPENING   -> setup in progress (inside subscribe, hops being held); no events yet
LIVE      -> events flowing
DEGRADED  -> a drop is being recovered (reconnect/idle-gap); events resume into the same handle
CLOSED    -> ended cleanly (cancel or EOF); closed_reason in {CANCELLED, EOF}
FAILED    -> ended on an unrecoverable error (reader death / reconnect budget exhausted);
             closed_reason == ERROR; this is the one ERROR-logged terminal (Logging rule 3)
```

`closed_reason: CANCELLED | EOF | ERROR`. There is exactly **one** state enum; earlier
section-local enums (DRAINING/DEAD/idle_gap/...) are subsumed: DRAINING is part of CLOSED's
teardown; idle_gap is a DEGRADED sub-case.

### 1.6 `Subscription` — the one user-facing handle

```text
class Subscription:
    # identity / correlation
    id: str                  # stable per-subscription id (the 'sub' log field) — new_txn()-style
    topic: str
    device: str              # the device path/id subscribed at
    # status (read-only)
    state: SubState
    closed_reason: ClosedReason | None
    last_gap: Gap | None     # immutable; updated by replacement to close a span
    alive: bool              # property (NOT named is_active — avoids Transport.is_active() clash)
    # delivery (consumer picks ONE form)
    def __iter__(self)  -> Iterator[Event|Gap|WatchdogTrip]      # events()
    def __aiter__(self) -> AsyncIterator[Event|Gap|WatchdogTrip] # stream()
    # control
    def cancel(self) -> None             # idempotent, thread-safe, callable from inside a callback
    def __enter__/__exit__               # context-managed lifetime (documented default)
```

`Subscription` is **registered with the owning `Hal`** (so teardown can cancel it) and is the
**named mux-pin holder** (so `shal.Busy` can name it). `cancel()` is idempotent, thread-safe,
and tears down *this* subscription's hops **leaf→root**; it never closes shared connections
(that is Phase D of `Hal.close`, §10).

### 1.7 Internal runtime objects & ownership map

| Object | What it is | Owned by | Guarded by |
|---|---|---|---|
| `ChannelHandle` | one hop's held resource (ssh `Popen`+pipe / mqtt client demux slot / socket) + an `unblock()`/`close()` | the `Transport` that opened it (the **leaf** for the source; `MuxChannel` for the mux hop) | that bus's `self.lock`, held **only** for short state mutations |
| `_SubBuffer` | bounded queue, **the** thread-safe enqueue point; owns the overflow policy | the `Subscription` | its own internal lock (multi-producer: reader **and** watchdog call `buffer.offer()`) |
| reader (thread) | the one reader per subscription; normalizes hop items → `_SubBuffer`; runs the reconnect loop; **never runs user code** | the `Subscription` | — (daemon thread) |
| mux pin | `MuxState.pinned_channel: int|None` + `pinners: set[sub_id]` + `pinned_by_desc: str` | `MuxState` (shared across a physical mux's channels — Phase 1) | `MuxState.lock`, held **transiently** only |
| watchdog | one timer thread + min-heap of deadlines + node→entry map | the `Hal` | the watchdog's own `Condition` |
| `Hal._subs` | registry of live subscriptions; `path -> set[Subscription]` index for trip routing | the `Hal` | a `Hal`-level lock |

**Producer model (resolves the single-writer conflict):** the `_SubBuffer` is the multi-producer
boundary. The reader is the *primary* producer; the watchdog thread is a *secondary* producer for
`WatchdogTrip`. Both call `buffer.offer(item)`; the **buffer**, not the reader, owns the bounded
overflow policy. No producer ever holds a bus lock while calling `offer()`.

---

## 2. The Stream transport-kind contract

### 2.1 `Stream` mixin (the stub, filled in)

```text
class Stream:                                  # stateless kind mixin
    multiplexes: bool = False                  # class attr: can this bus carry a held stream
                                               #   concurrently with sync on ONE connection?
    def subscribe(self, addr, topic, *, sink, setup_timeout, idle_timeout) -> ChannelHandle: ...
    def supports_stream(self) -> bool:         # honesty for FORWARDING hops (mux); default True
        return True
```

`Stream` joins `kinds()` by `isinstance` — **already wired** (`transport.py:kinds()` iterates
`(ByteTransport, CommandTransport, MessageTransport, Stream)`). **`kinds()` stays pure
`isinstance`** — a `MuxChannel` *always* mixes `Stream`, and honesty for forwarders comes from
`supports_stream()` (which a mux defines as "does my upstream support a stream"), **not** from a
conditional `kinds()` override. This preserves the invariant the loader's kind-check depends on.

`subscribe` stays in `Driver._PLUMBING` (already there) → it is **not** wrapped by the capability
wrapper (it returns a held handle, not a value; it must not get idempotent-retry or a bogus
audit).

### 2.2 Weakest-hop validation (Rule 1) — split into two checks

| Check | When | Failure |
|---|---|---|
| **Unconditional**: does every hop on the path support a held stream? (`Stream in kinds()` and, for forwarders, `supports_stream()`) | **at setup** (a leaf→root `kinds()` walk, no I/O) | `StreamUnsupported(LoadError)` naming the first weak hop |
| **Conditional**: a non-multiplexing connection hop is asked to carry a held stream *and* concurrent sync | **at runtime**, at the moment of the conflicting sync call | `shal.Busy` naming the holding subscription |

> Why the split (resolves a real contradiction): the unconditional part is statically decidable
> from topology + `kinds()`, so it fails loudly at setup as Rule 1 demands. Whether a *sync*
> caller will later share a non-multiplexing stream hop is **not knowable at subscribe time**, so
> forcing it into setup would be dishonest; it surfaces as a named runtime `Busy`, identical UX to
> mux pinning. For every in-scope family this conditional path is a forward guard (ssh
> multiplexes; mux uses pinning).

### 2.3 Setup: open order, atomic rollback

- **OPEN is root→leaf** (a child held channel cannot exist before its parent's channel — same as
  sync activation), realized by the leaf→root `upstream` recursion calling the parent's open
  first.
- **VALIDATE is a leaf→root `kinds()` walk** (cheap, no I/O).
- **ROLLBACK/CLOSE is leaf→root** (mirror of open).
- `subscribe` **blocks until every hop is open** (the *setup* phase) bounded by `setup_timeout`;
  on a later-hop failure it **unwinds the already-opened hops leaf→root**, swallows per-hop unwind
  errors (WARNING), **re-raises the original** setup error, **never starts a reader**, and **leaves
  shared connections open** (so sync keeps working on the same path). No orphan channels, no leaked
  mux pins, no stranded threads.

After setup returns, events flow asynchronously into the `Subscription`. This **synchronous
setup / asynchronous delivery** split is what lets the sync-first `events()` API exist without an
event loop: setup is a normal blocking call; delivery is fed by one background reader.

### 2.4 Per-kind delegation through a mux

A `MuxChannel` is a forwarding hop: its `subscribe` pins its channel and **delegates to its
upstream's `subscribe`** (it owns no source; it relays its parent's stream — exactly the pattern
`MuxChannel.txn` already uses for sync). Pin acquisition is **transient-locked**, never held
across the upstream open (§4.3).

### 2.5 ssh realization (held streaming command)

The ssh stream renders a **long-running argv** (`tail -F -- <path>`, or a persistent reader) over
**one held exec channel per subscription** (Rule 3), decoded back — nothing installed far side,
**argv only, no shell ever** (the security keystone). Concretely, and unlike Phase 1's one-shot
`subprocess.run`:

- streaming ssh uses **`subprocess.Popen`**, retaining the `Popen` on the `ChannelHandle`;
- the reader loops on `Popen.stdout.readline()`;
- the streaming child runs as a **new exec channel over the same ControlMaster** master, so
  killing it does **not** tear the master and Phase 1 sync `run` keeps working;
- `cancel()`/teardown calls `Popen.terminate()` then `kill()` after a grace period, then closes
  stdout — **closing stdout is what unblocks the reader** parked in `readline()`;
- the shared ControlMaster session is **refcounted**; the last subscription/sync user closes it;
- a remote process death (EOF, `exit != 0`, ssh exit `255`) is a **drop-to-recover** (re-spawn the
  same argv), **not** a clean stream end — only `cancel()`/HAL teardown ends a subscription
  cleanly (no `Gap` on cancel). `ssh,host` declares `multiplexes = True`.

---

## 3. User-facing API

### 3.1 Declaration: a streaming capability

Streamability is **inferred from the driver**, never declared in YAML (locked: capabilities come
from the driver's Protocols). A driver opts in by mixing the streaming surface and marking its
event source:

```text
class Cleaner(shal.Driver, shal.StreamingMixin):     # gains events()/stream()/subscribe()
    stream_topics = ("status", "pose")               # declared topics (validated at subscribe)

    @shal.streaming("status")                         # stamps __shal_stream__; NOT an LLM tool
    def _status_source(self, channel) -> Iterator[Event]: ...
```

The three user methods live on `shal.Driver` **base** (so the call site is uniform), but a
**non-streamable** device's `events()`/`stream()`/`subscribe()` raises
`StreamUnsupported(LoadError)` at the call with a clear "device declares no stream topics" message
(resolves the base-vs-mixin contradiction: base methods + a topic-declaration check, not a missing
attribute / `AttributeError`).

**LLM-tool exclusion (highest-risk Phase-1 seam):** `events`, `stream`, `subscribe` are added to
`Driver._PLUMBING` (name-based, override-proof — the **primary** guard), and any streaming
*producer* method must be `_`-prefixed or carry `@shal.streaming` (`__shal_stream__`), which
`capability_ops()` skips. A held, multi-event subscription cannot be a `call_tool` one-shot, so
streaming is **excluded** from the callable `tool_schemas()` and surfaced instead in a
**descriptive, non-callable `stream_catalog()`** (so an agent knows events exist; the callable
seam waits for the agent bus). A conformance test asserts `tool_schemas()` contains no streaming
method for a streaming device.

### 3.2 The three forms

```text
# blocking iterator — scripts, notebooks (no event loop)
for ev in dev.events("status", idle_timeout=...):
    if isinstance(ev, shal.Gap): continue
    if isinstance(ev, shal.WatchdogTrip): ...
    handle(ev)

# asyncio mirror — apps
async for ev in dev.stream("pose"): ...

# the primitive both are built on
sub = dev.subscribe(cb, topic="status", setup_timeout=..., idle_timeout=..., on_error=...)
sub.cancel()
with dev.subscribe(cb, "status") as sub: ...     # context-managed (documented default)
```

Canonical signature (the cb/topic order was swapped between two drafts — fixed):
`subscribe(self, callback, topic='', *, setup_timeout=None, idle_timeout=None, on_error=None)`
— **callback-first**, matching the locked `dev.subscribe(cb)` example. `events()`/`stream()` are
adapters that feed an internal buffer (`subscribe(callback)` is the single primitive).

### 3.3 Iterator-end semantics (per cause)

| Cause | What the loop sees |
|---|---|
| **EOF** (clean source end) | drains buffered events, then ends cleanly (`StopIteration` / `StopAsyncIteration`). EOF is not a failure → raising would lie. |
| **Cancel** | drains buffered events, then ends cleanly. No `Gap` on cancel (no events after `cancel()` returns). |
| **Error** (reader death / reconnect budget exhausted) | a trailing inline `Gap` is delivered, then the next step **raises `HopError`** (path/hop/txn/delivered) — the "must know, not be lied to" case, hop-attributable like sync. |

### 3.4 Timeouts (two distinct numbers — locked)

- **setup_timeout** — governs the open phase only; raises `HopTimeout` at the call site (fail
  loudly at setup). Per-call or bus-config; mirrors the sync `min(hop, budget)` model.
- **idle_timeout** — governs the steady phase only; default behavior `idle_policy = gap` (emit a
  `Gap(reason='idle')`, stay `LIVE`); opt-in `error` (treat as drop → reconnect). They **never
  overlap** (setup ends before steady begins).

Stream tuning lives under **`config: { stream: { idle_ms, idle_policy, setup_ms, reconnect_*,
queue_max, overflow } }`** — **no schema change** (the schema already documents `config:` as the
home for bus/driver-specific params and validates string/number/bool). Precedence:
**call-site > node `config` > framework default**. Both `idle_ms` and `idle_policy` are
per-call-overridable (one consistent rule).

---

## 4. Runtime: reader, asyncio bridge, backpressure, correlation

### 4.1 The reader (one per subscription)

A single dedicated **reader thread** per subscription: it reads hop items, normalizes them into
`Event`/`Gap`, calls `buffer.offer(...)`, and runs the reconnect loop. It **never runs user
code** — that is what makes "consumers must not block dispatch" structural. `events()` drains the
buffer on the consumer's thread; `stream()` bridges to asyncio (§4.2); `subscribe(cb)` invokes the
callback on a consumer-side dispatch, **never** on the reader thread.

### 4.2 asyncio bridge — SHAL owns no loop

`stream()` binds to the **caller's running loop**, captured at `__aiter__` via
`asyncio.get_running_loop()`. Per-item handoff is `loop.call_soon_threadsafe(queue.put_nowait,
item)` onto a **loop-owned `asyncio.Queue`**; the reader touches that queue **only** through
`call_soon_threadsafe` (the only thread-safe door). `events()` and the reader are
**pure-threading** — no `asyncio` import on that path. (Locked: "an event loop near a bringup
script is the worst possible ergonomics regression.")

Loop edge cases: if the loop is closed/closing when an item is scheduled, the reader catches the
`RuntimeError`; and **`sub.cancel()` from any thread** is the always-available escape hatch that
schedules a final sentinel and, if the loop is dead, the sync teardown path still closes hops — so
an `__anext__` can never wedge forever. Non-asyncio loops (Trio/anyio) are a **forward seam** (a
pluggable loop adapter); asyncio is the only async target in DESIGN V2.

### 4.3 The CRITICAL lock rule (a bug the review caught)

> **The reader MUST NOT hold any bus lock across a blocking wire read.** It acquires the bus
> `self.lock` **only** for short state mutations (`_active`/cache flips on EOF/half-open), and the
> blocking `recv`/`readline` happens on the stream's **own held resource** (the `ChannelHandle`'s
> fd / `Popen.stdout` / mqtt client), with **no bus lock held**.

A streaming read blocks until an event arrives — possibly hours of idle. If the reader held the
one-RLock-per-bus across that read, **every concurrent sync call on a multiplexing hop would
block forever** (RLock is reentrant only within one thread; reader and sync caller are different
threads). That would falsify the design's own headline guarantee ("sync and async coexist on a
multiplexing hop"). The held streaming resource being **separate** from the sync round-trip path
(true for ssh exec channels and mqtt) is what makes coexistence real.

### 4.4 Backpressure (your ruling: configurable, default drop-oldest)

A **bounded per-subscription queue** (`queue_max`, default **256**), **configurable** overflow
policy, default **`gap_oldest`**: drop the **oldest** buffered events and surface one **coalesced
`Gap(reason='overflow', local=True)`** (Rule 4 — never a silent drop). Options: `gap_newest`,
`block`, `error`. The reader is **never blocked** by a slow consumer (locked: consumers must not
block dispatch; blocking the reader would back-pressure the shared/multiplexed hop and stall
concurrent sync). Drop-oldest keeps the **freshest** sample — right for telemetry/pose/status and
for safe-state consumers. The coalescing overflow `Gap` is **held back until its window closes**
(§1.3) so a delivered `Gap` is never mutated.

### 4.5 Correlation — two ids (resolves the txn conflict)

Streams use **two** correlation ids:
- **`sub`** — a stable per-subscription id, set **once** at reader start into a new
  `current_sub` contextvar (never reset — the reader owns its thread/context). It is the greppable
  unit for the whole stream (events, gaps, reconnects).
- **`txn`** — a **fresh** id minted **per dispatched event** into the existing `current_txn`
  contextvar, with `reset` in `finally` (so nested sync calls from a callback still compose
  correctly). `(path, hop, txn)` stays the event identity (Rule 9); `sub` sits above it.

The reader carries context via `contextvars.copy_context()` captured at `subscribe()` and runs its
body under `ctx.run(...)` (contextvars do **not** auto-propagate to a spawned thread). This is the
one place the earlier drafts disagreed three ways — the **two-id model is canonical**; any draft
that put the subscription id into `current_txn` is superseded (it goes into `current_sub`).

---

## 5. Concurrency & locking

- **`multiplexes`** is a **class attribute** (`bool`, default `False`) on the bus — a static
  property of the wire family, like `kind`/`compatible`, introspectable at setup without a live
  connection. Default `False` is the safe answer (interleaving a sync round-trip into a single
  held byte stream corrupts both). `ssh,host` and `shal,sim-stream` declare `True`.
- **Lock discipline:** the reader holds the bus lock **only** for the produce-state mutation
  (flip `_active`, never across `recv` — §4.3); user code runs consumer-side holding **no** bus
  lock (a callback may legally re-enter `exchange()` → it takes a fresh leaf→root acquisition).
  Lock acquisition still follows the recursion (**deeper-before-shallower**) → no cyclic wait.
- **`_active`/`is_active`** reads/writes stay under `self.lock` (including the reader's EOF/half-
  open updates); `ensure_ready()` collapses concurrent reconnects to exactly one `activate()`.
- **Mux pinning (canonical model):** `MuxState.pinned_channel: int|None` + `pinners: set[sub_id]`
  + `pinned_by_desc: str`, guarded **transiently** by the existing `MuxState.lock`:
  - a subscription pins its channel for its lifetime; a **sibling** sync `txn` on a *different*
    channel raises `shal.Busy` **naming the holding subscription** (checked inside `MuxChannel.txn`
    **before** `ensure_ready()`, because `activate()` is what physically re-selects);
  - **same channel** may hold **many** subscriptions (refcounted via `pinners`); pin released on
    **last** cancel;
  - a **second different channel** subscribe → `Busy` at setup (a physical mux selects one channel;
    two held streams on different channels would time-slice the select and corrupt both);
  - **same-channel multi-subscription is allowed only for native-pub/sub leaves that demux
    internally** (mqtt). On a **per-stream byte leaf** behind a mux, a second same-channel
    subscription is **rejected at setup** (two blocking readers on one selected byte wire would
    interleave and corrupt — sync `txn`s are serialized by `state.lock`, blocking stream reads are
    not).
  - **Mux subscribe does NOT hold `MuxState.lock` across I/O:** reserve the pin under a short lock,
    release, run the slow `upstream.subscribe` **lock-free**, re-acquire briefly to finalize (or
    roll back the reservation on failure). Holding the lock across a multi-second open would freeze
    the whole physical mux.

---

## 6. Drops, Gap surfacing, same-path reconnect

(Failover is out of scope — only reconnect/re-subscribe on the **same** path.)

- The **one reader** is the single mint point for `Gap` and the **sole owner of the reconnect
  loop**. Stream reconnect does **not** reuse `driver._make_call`'s sync retry wrapper (that is
  per-call, keyed on `HopError.delivered`/`@idempotent`; a held stream is set up once then pumps).
- A hop drop unwinds to the reader via a **private** `_Dropped(hop, reason, since)` exception
  (kept out of the public taxonomy — only the reader knows the subscription identity and may mint a
  public `Gap`; this preserves "Gap is an event, never raised").
- On drop: emit `Gap(reason='drop', hop, since=last_event_ts, until=None)` **immediately** →
  `DEGRADED` → re-open the held channel **on the same path** (same address/`parent_bus`) →
  resume `LIVE`. `_next_route` is a **stub seam** (failover later).
- **Reconnect schedule:** jittered exponential backoff (default 200 ms … 30 s, full jitter to avoid
  a thundering herd when many subscriptions share one dropped hop), bounded by a **consecutive-
  failure budget** (default **5**; `0` = none, `-1` = forever), the counter **resetting to 0 on
  each successful reconnect** (a link that flaps but recovers never exhausts). The `setup_timeout`
  applies **per attempt**. Budget exhausted → `FAILED` (the terminal error case, §3.3).
- **Idle:** default `gap` (emit `Gap(reason='idle')`, keep the channel); opt-in `error` (treat as
  drop → reconnect); `none` when `idle_ms` unset. During a long reconnect the idle `Gap` is the
  liveness signal (slower cadence than steady idle to avoid queue/log noise).
- **Overflow (slow consumer)** surfaces as `Gap(reason='overflow', local=True)` — the *same*
  honest data-loss signal as a wire drop (one mental model: a `Gap` means a span was lost, for
  whatever reason; `local` disambiguates).

---

## 7. Watchdog & safe-state (actuator deadman)

### 7.1 Mechanism

- **One timer thread per `Hal`** (locked), holding a **lazy-decrease-key min-heap** of monotonic
  deadlines `(deadline, seq, entry)`; stale tuples discarded at pop. O(1) "soonest deadline",
  sleeps exactly until due, zero busy-poll. `feed()` on the hot path is one in-place deadline write
  + a `Condition.notify` only when the fed node is the heap root.
- **All timing is `time.monotonic()`** (immune to NTP/DST). System suspend/resume → armed
  watchdogs and idle timers fire on resume (treat "we were asleep" as "we were silent" — the safe
  direction for an actuator). A **mass-trip on resume** is possible; that is the safe failure
  mode, but see open questions on load-shedding.

### 7.2 What feeds the deadman (resolves the "feed gating" gap)

Only **successful state-changing commands** re-arm: `side_effect in {write, actuator}`. Reads and
inbound stream **events** do **not** feed (a deadman detects *commanding* silence, not traffic;
polling a sensor must not be mistaken for "still in control of the motor"). A read-only / stream-
only device with `watchdog_ms` set **trips by design** under the connection-loss trigger.

> Implementation note the deltas understated: `_make_call` does **not** currently compute
> `side_effect` (it only has `__shal_idempotent__`). Phase 2 must have `_make_call` resolve
> `side = (fn.__shal_op__ or {}).get('side_effect') or ('none' if idempotent else 'write')`
> **once at wrap time** (mirroring `hal._effect`, ideally a shared helper), store a per-op
> `feeds_watchdog: bool`, and call `watchdog.feed(entry)` only when `feeds_watchdog and the node
> has a watchdog entry`. The **retry-then-success** path **does** feed (the command ultimately
> succeeded). Bus helpers (Transport instances) never feed.

### 7.3 The three triggers and how buses report

| Trigger | Source |
|---|---|
| **silence** | the timer-thread deadline expires (no qualifying command within `watchdog_ms`) |
| **connection loss** | a bus calls `note_connection_loss(self)` at the exact site it already sets `_active=False` |
| **hop failure** | a `HopError` on a watched path |

`note_connection_loss()` is a **bind-injected, no-op-defaulted** callback (buses work in unit
tests / sim with no `Hal`). It is **safe to call while the bus's own lock is held** — it only
**appends + notifies**, never runs `safe_state` inline. The watchdog only **schedules** trips;
`safe_state()` runs **on the watchdog's own thread (or a dispatch worker), holding no bus lock the
reporting bus could be holding**. (Running `safe_state` inline under a bus lock would deadlock — it
re-enters the bus.) This rule is **normative** and the deltas reference it.

### 7.4 `safe_state()` contract

Idempotent, **must not raise**, best-effort write through `self.bus`. A raise is the **one
legitimate ERROR-level log** in this subsystem (background failure, no exception path). The base
`Driver.safe_state` is a no-op; the loader **warns at load** if a `watchdog_ms` node's driver does
not override it.

### 7.5 Dead link (your ruling: log + mark, honest limit)

When the link is severed, the watchdog **commands** `safe_state()` once, **marks engaged**, logs
**WARNING `reachable=false`**, and surfaces `WatchdogTrip(reachable=False)` to active
subscriptions. It **does not claim physical safety**:

> **Doctrine.** A network watchdog bounds what a **live** link leaves an actuator doing
> (≤ `watchdog_ms` of unattended motion while the link is up) and **loudly surfaces** a down link.
> It physically **cannot** reach through a severed link; pretending otherwise (logging a false
> success) is the dangerous lie. **Safety-critical actuators must also have an on-device
> failsafe** — that is mandatory, not optional. *(Forward seam: re-fire `safe_state` on reconnect
> — noted, not built.)*

### 7.6 Mux-pinned device (resolves O1 consistently with 7.5)

If a watched device sits behind a mux channel **pinned by a different device's subscription**,
`safe_state()` **cannot be delivered** (it would `Busy` on the pin). Per the honest-limit doctrine
and the locked "**don't silently yank a held stream's channel**", SHAL does **not** preempt the
pin: it logs **WARNING `reachable=false`** and surfaces the trip. (Preemption was considered and
**rejected** — silently re-pinning a sibling's held stream for a safe_state contradicts the locked
rule and the dead-link doctrine; an actuator that can't be reached without yanking another
device's stream is exactly the case the on-device failsafe exists for.)

### 7.7 Re-arm, surfacing, arming

- An **engaged** node does **not** auto-rearm on reconnect; **only a subsequent successful
  command** clears engaged (a flapping link must not silently un-safe an actuator). A dropped link
  trips the watchdog **immediately and independently**; a later stream reconnect does **not**
  rearm it (intended — a dropped link to an actuator is a safety event even if telemetry resumes).
- Trips surface as **`WatchdogTrip`** (§1.4) onto the subscription buffer via `buffer.offer()`
  (non-blocking; on a full queue, coalesce into a Gap rather than block the safety thread).
- **Initial arming: on the first successful command** (the silence cadence only exists once
  commanding starts; a never-commanded actuator has no window, and lazy-open means the link may
  not be up). The connection-loss / hop-failure triggers fire for any `watchdog_ms` node
  regardless of arming. *(Arm-at-bind was considered for strict safety — flagged in Open Questions
  for sign-off, as it interacts with lazy connection open.)*
- Each trip mints a **fresh framework-originated `txn`** so the trip's `safe_state` hops correlate.

### 7.8 Off-thread dispatch

The timer thread pops deadlines and **hands `safe_state()` to a dispatch** so one slow/blocking
`safe_state` cannot delay another node's due deadline. "One timer thread" governs *scheduling*;
*execution* may use a small worker. (Marked a refinement; for a single watched actuator the timer
thread can run it inline.)

---

## 8. Lifecycle & teardown

**Phase order** (honors locked "cancel subs → close connections leaf→root → release locks"):

```
A. watchdog.disarm_all()         # stop NEW trips; thread KEEPS running
B. cancel ALL subscriptions      # leaf→root per sub; a trip mid-B can still surface to a draining reader
C. watchdog.stop() + join        # now no more trips
D. _close_subtree leaf→root      # UNCHANGED from Phase 1; runs against provably-idle transports
```

Disarming first prevents a teardown-induced `safe_state` from cancel-silence or a half-closed hop;
keeping the thread alive across cancel lets a draining reader still see a trip; joining readers
before connection close means Phase 1's `_close_subtree` runs against idle transports.

- **`cancel()`** = cooperative stop signal + **forced unblock** of the parked read (terminate
  `tail -f`, mqtt unsubscribe, close stdout/fd, sentinel) + **bounded join**. On failure to stop:
  **leak as a daemon thread + ERROR log, never hang, never raise** (Python cannot safely kill a
  thread; the subsequent connection close usually faults the stuck read so the orphan exits).
- **Per-subscription channel close happens in Phase B (`cancel`), connection close only in Phase
  D.** Separating them makes double-cancel, partial cancel, and shared-hop teardown safe and keeps
  lock order deeper-before-shallower. The mux pin is released under `MuxState.lock` during channel
  close — the defined point a sibling's `Busy` clears.
- Reader and watchdog threads are **`daemon=True`**; `Subscription` teardown also registered via
  `weakref.finalize` (capturing only the stop plumbing, not strong refs) + a module-level `atexit`
  hook. **The `with`-form is the only HARD guarantee.**
- **Process-exit honesty (cross-referenced from the watchdog doctrine):** a daemon watchdog thread
  is killed at interpreter exit **without a final trip** → `safe_state` is **not guaranteed** on
  exit → residual safety is the **device-local deadman**. *(Optional: an `atexit` best-effort
  `safe_state()` over armed actuators before the daemon dies.)*
- **Abandoned-but-uncancelled live subscription:** a running reader **pins** its `Subscription`
  (it is a GC root), so weakref/GC cleanup only fires **after** the reader exits (EOF/error). A
  still-streaming abandoned sub **leaks until `Hal.close()`**, bounded only by its queue — stated
  honestly; the only cleanup is `cancel()` / `with` / `Hal.close()`.

---

## 9. Concrete deltas to Phase 1 (additive, file-by-file)

| File | Change |
|---|---|
| `src/shal/stream.py` (NEW) | `Event`, `Subscription`, `ChannelHandle`, `_SubBuffer`, the reader, `WatchdogTrip`, `StreamingMixin`, `@streaming`. Stateful runtime — kept out of `transport.py` (whose contract is "only `Transport` has state"). |
| `src/shal/watchdog.py` (NEW) | one `Watchdog` per `Hal`: timer thread, min-heap, `feed`/`note_connection_loss`/`disarm_all`/`stop`, dispatch. |
| `transport.py` | fill in `Stream.subscribe(addr, topic, *, sink, setup_timeout, idle_timeout) -> ChannelHandle`; add `Stream.supports_stream()`; add class attr `multiplexes: bool = False`. `kinds()` unchanged (stays pure `isinstance`). |
| `errors.py` | widen `Gap` with optional fields (§1.3, backward compatible); add `StreamUnsupported(LoadError)`. |
| `driver.py` | `events`/`stream`/`subscribe` on `Driver` base (raise `StreamUnsupported` if not streamable); add `events`/`stream` to `_PLUMBING`; `_make_call` computes `feeds_watchdog` once at wrap time and calls `watchdog.feed` on the success branch for write/actuator ops; exclude `@streaming`/`StreamingMixin` producers from `capability_ops()`. `safe_state` already exists. |
| `hal.py` | own the `Watchdog`; `Hal._subs` registry + `path -> set[Subscription]` index (locked); teardown phases A–D (§8); inject `note_connection_loss` into buses at bind. `tool_schemas()`/`call_tool()` unchanged but verified to exclude streaming; add descriptive `stream_catalog()`. |
| `node.py` | declare `spec`, `_watchdog_entry`, `_subs` as real slots in `__init__` (avoid monkey-patched attrs / 2 a.m. `AttributeError`s; mirror `sim.py`'s defensive `getattr` otherwise). |
| `buses/mux.py` | `MuxChannel` mixes `Stream`; `supports_stream()` = upstream supports stream; `subscribe` delegates upstream with transient-locked pin; `MuxState` gains `pinned_channel`/`pinners`/`pinned_by_desc`; `txn` raises `Busy` on sibling pin before `ensure_ready`. |
| `buses/ssh.py` | streaming `subscribe` via `Popen` over the shared ControlMaster (§2.5); `multiplexes = True`; `note_connection_loss` at the existing `_active=False`/exit-255 sites. |
| `buses/sim.py` | **new `SimStreamBus` (`shal,sim-stream`)** — do NOT add `Stream` to `SimI2cBus` (it must stay the non-streaming hop that makes the "async behind non-streaming i2c fails at setup while sync works" conformance test demonstrable). |
| `buses/tcp.py` | `note_connection_loss` at its drop sites (must be safe under `self.lock`); `multiplexes` stays `False` (rely on the runtime `Busy` guard). |
| `schema/shal-v1.schema.json` | **no structural change** — stream tuning lives under existing `config:`; only docs updated. `watchdog_ms` already parses (Phase 1 no-op) → now wired. |
| `log.py` | new stable `event` keys (§11); add `current_sub` contextvar (the `sub` field). |
| `__init__.py` | export `Subscription`, `Event`, `WatchdogTrip`, `StreamUnsupported`, `streaming` (Gap/Busy already exported). |

**Streamability is inferred** (`isinstance(driver, StreamingMixin)`); the loader's stream
validation pass runs **only** for such nodes, so the entire Phase 1 sync tree is never walked
(zero cost). Per-topic existence is checked at `subscribe` against `stream_topics` (declared,
load-visible).

---

## 10. Observability

Consistent with the locked logging rules (stdlib logging, never configured; raise-or-log; ERROR
reserved).

- **Two ids** (§4.5): `sub` (stable per subscription) nested above the per-event `txn`. Both reuse
  `new_txn()`; no new id scheme.
- **Levels** (locked rule 4 buckets): `subscription.start`/`stop` = **INFO**; `gap`, `reconnect`,
  `watchdog.trip` = **WARNING**; `watchdog.rearm` = **INFO** (recovery is lifecycle, and omitting
  it leaves operators unable to tell a still-tripped node from a recovered one). One start/stop
  record **per hop** plus a leaf summary ("stream up").
- **The one ERROR site (rule 3):** `reader.error` — a background reader dying that cannot surface
  as an exception to any caller. A **user callback raising** inside `subscribe(cb)` is **caught,
  ERROR-logged with full context, and the reader continues** (killing the reader on one bad
  callback violates "callbacks must not block/kill dispatch"; catch+ERROR+continue honors both
  rules and is not double-reporting).
- **Audit:** watchdog `safe_state` trips emit on **both** `shal.watchdog` (WARNING, operator) and
  `shal.audit` (WARNING — escalated above the INFO command records, so a single `shal.audit` grep
  tells the whole actuator story: every command *and* every safe-state).
- **`capture()`** stays a bounded recording window (does **not** join/drain readers on exit — that
  is `Hal.close`'s job). Records emitted after block-exit are dropped (a debug recorder, not
  stream-lifetime observability); the handler/level mutation can benignly race a concurrent
  background-thread emit (a few records land on the old/new handler — acceptable). An optional
  `max_records` ceiling + `capture.truncated` marker is a **forward seam** (default off; not built
  this phase — keeps Phase 1 tests pinned).
- **Metrics/otel:** the hop boundary is the single instrumentation point (forward-compatible); the
  stream label set (`sub`, `topic`, `hop`, gap span) is fixed here, the API is not (still open).

---

## 11. Edge-case & failure matrix

Defined behavior for every nasty case. Grouped; `→` is the contract.

### Setup / weakest-hop
| # | Case | Behavior |
|---|---|---|
| A1 | subscribe through a hop lacking `Stream` | `StreamUnsupported(LoadError)` at setup, naming the first weak hop. **Sync still works on the same path.** |
| A2 | subscribe on a non-streamable **device** (hops all stream-capable) | `StreamUnsupported(LoadError)` at the call — "device declares no stream topics". |
| A3 | subscribe a held stream + concurrent sync on a **non-multiplexing** connection hop | setup succeeds (unconditional check passes); the **conflicting sync call** raises `shal.Busy` at runtime. |
| A4 | a later hop fails mid-setup (k hops open, k+1 fails) | unwind opened hops **leaf→root**, swallow unwind errors (WARNING), re-raise the **original** error, no reader started, shared connections left open. |
| A5 | unknown `topic` not in `stream_topics` | `StreamUnsupported(LoadError)` at subscribe (load-visible declaration). |

### Mux / pinning
| # | Case | Behavior |
|---|---|---|
| B1 | second subscribe on a **different** channel of one mux | `shal.Busy` at setup (single physical select). |
| B2 | second subscribe on the **same** channel, native-pub/sub leaf | allowed; refcounted in `pinners`; pin released on last cancel. |
| B3 | second subscribe on the **same** channel, **per-stream byte** leaf | **rejected at setup** (two blocking readers would corrupt one byte wire). |
| B4 | sync `txn` to a **sibling** channel while pinned | `shal.Busy` naming the holding subscription (checked before `ensure_ready`). |
| B5 | watchdog `safe_state` to a device behind a **sibling-pinned** mux | **not delivered**; WARNING `reachable=false` + `WatchdogTrip(reachable=False)`. No pin preemption (§7.6). |
| B6 | same-path reconnect re-pins the mux | during the brief reservation window a sibling sync racing the re-pin is serialized by `MuxState.lock`; the reconnect re-reserves before re-selecting. No silent sibling select into the gap. |

### Backpressure / queue
| # | Case | Behavior |
|---|---|---|
| C1 | slow/blocking consumer | bounded queue; default `gap_oldest` (drop oldest + coalesced `Gap(overflow, local=True)`); reader never blocks. |
| C2 | overflow `Gap` racing a wire-drop `Gap` | overflow Gap held until window closes; the wire-drop Gap (immediate) orders ahead; two distinct Gaps, `local` disambiguates. |
| C3 | callback re-enters `exchange()` | normal fresh leaf→root acquisition (reader holds no bus lock during dispatch). |
| C4 | callback calls `sub.cancel()` on its own subscription | allowed (cancel is reentrant/thread-safe); in-flight items after `_stop` are suppressed; loop ends cleanly. |

### Watchdog / safety
| # | Case | Behavior |
|---|---|---|
| D1 | `safe_state` to a **dead** link | commanded once, mark engaged, WARNING `reachable=false`, surface trip; **no false success** (§7.5). |
| D2 | read-only / stream-only node with `watchdog_ms` | not fed by events/reads; trips under connection-loss; silence-arming only after a first command (§7.7). |
| D3 | `safe_state` itself blocks/slow | off-thread dispatch so other nodes' deadlines aren't starved (§7.8). |
| D4 | two watched nodes behind one hop that dies once | both scheduled; bounded per-node `safe_state`; total subtree trip latency bounded by the dispatch (not serialized full-timeouts). |
| D5 | suspend/resume (monotonic counts through) | all armed watchdogs + idle timers fire on resume (safe direction); mass-trip is the safe failure mode (load-shed is open). |
| D6 | `feed()` races a trip pop | the heap discards stale tuples at pop; the entry's `armed`/`engaged` flags are checked under the watchdog lock so a just-commanded node is not spuriously safed. |
| D7 | `safe_state` not overridden on a `watchdog_ms` node | WARNING at load (base no-op would silently do nothing). |

### Cancel / teardown
| # | Case | Behavior |
|---|---|---|
| E1 | double `cancel()` / cancel during `__exit__` | idempotent no-op. |
| E2 | cancel during reconnect backoff | the backoff wait is `_stop`-interruptible; teardown wins; trailing Gap suppressed. |
| E3 | reader won't stop on cancel | bounded join → leak as daemon + ERROR; never hang, never raise; connection close usually faults it out. |
| E4 | `Hal.close()` mid-stream / mid-drop | phases A–D; disarm → cancel (suppress trailing Gap) → join → close. |
| E5 | process exit, bare `load()` | best-effort atexit/finalizers; **no guaranteed final `safe_state`** → device-local failsafe is the residual (§8). |
| E6 | abandoned live sub (never cancelled) | leaks until `Hal.close()` (reader pins it, defeating GC) — stated, bounded by queue. |

### asyncio / threading / correlation
| # | Case | Behavior |
|---|---|---|
| F1 | `stream()` iterated on a closed/closing loop | reader catches `RuntimeError`; `sub.cancel()` from any thread is the escape hatch (sentinel + sync hop close) so `__anext__` can't wedge. |
| F2 | `stream()` constructed on a loop thread (eager sync setup) | setup blocks that thread — documented; construct off the loop or accept the blocking open (open question: lazy setup at first `__aiter__`). |
| F3 | reader thread crashes | `reader.error` ERROR + a trailing Gap + `FAILED`; a pulling consumer's next step raises `HopError`; callback consumers get the ERROR log only. |
| G1 | contextvars in the reader thread | `copy_context()` captured at subscribe; `current_sub` set once, `current_txn` fresh per event — no cross-thread leakage, no `'----'` default window. |
| G2 | a node that is **both** a bus and a streamable device (`Driver+Transport+Stream`) | the transport `Stream.subscribe(addr, topic, ...)` and the device `subscribe(callback, ...)` are different methods; the device helper is the wrapped/excluded one — documented; if a real such node appears, the device helper is renamed to avoid MRO ambiguity. |

### Drops / reconnect
| # | Case | Behavior |
|---|---|---|
| H1 | ssh `tail -f` EOF / exit≠0 / 255 | drop-to-recover (re-spawn same argv on same master), not a clean end; check `_cancelled` first to avoid a spurious Gap on teardown. |
| H2 | reconnect budget exhausted | `FAILED` + trailing Gap + raise on next pull. |
| H3 | idle during a long reconnect | one idle `Gap` at a slower cadence (avoid queue/log flood). |
| H4 | mqtt broker drop Gaps all derived subs | **one** client reconnect (shared connection), Gaps fanned to all subs — not N parallel `client.reconnect()` (jittered, shared). |
| H5 | two subs share one ControlMaster; one cancels | per-sub exec-channel teardown; the session is refcounted; cancelling one does not disturb the other's exec channel (only the last user closes the master). |

---

## 12. Decision log (resolved)

| # | Decision | Why |
|---|---|---|
| 1 | Layered: transport `subscribe → ChannelHandle`; framework builds the **one** `Subscription`+reader | only model that fits Phase 1's one-reader rule |
| 2 | One canonical `SubState`/`Event`/`Gap`/`WatchdogTrip`/`Subscription` (§1) | the review's #1 ask — 8 sections had divergent shapes |
| 3 | Setup rejection = `StreamUnsupported(LoadError)` (not `HopError`) | structural, pre-data, deterministic from topology+kinds — the LoadError category |
| 4 | `multiplexes` = class attr; coexistence split: unconditional→setup `LoadError`, conditional→runtime `Busy` | static fact at setup; sync-sharing not knowable at subscribe time |
| 5 | Backpressure **configurable, default `gap_oldest`**, HWM 256 (your ruling) | freshest sample wins; surface loss as Gap (Rule 4); never block the wire |
| 6 | Two-id correlation: `current_sub` once + fresh per-event `current_txn` | a long-lived stream can't ride a per-call txn; keeps event identity + a greppable stream unit |
| 7 | Reader **never** holds a bus lock across a blocking read (§4.3) | the critical bug — otherwise coexistence on a multiplexing hop deadlocks |
| 8 | Mux pin: single `pinned_channel` + refcounted `pinners`; transient lock only; byte-leaf same-channel rejected | satisfies "reject 2nd channel" + "refcount same channel"; never freezes the mux |
| 9 | Open root→leaf, validate leaf→root, rollback/close leaf→root | a child channel can't exist before its parent's |
| 10 | Watchdog dead-link / mux-pinned: **log+mark, honest limit, no pin preemption** (your ruling) | a network watchdog can't reach a severed link; don't lie, don't yank a held stream |
| 11 | Trip surfaced as distinct `WatchdogTrip` (not `Gap('watchdog')`) | data-loss ≠ device-safed |
| 12 | `feed()` gated on `side_effect ∈ {write,actuator}`, computed once at wrap | a deadman detects commanding silence, not traffic; needs a real new read of `__shal_op__` |
| 13 | Stream config under `config:` (no schema change) | `config:` is the documented home; avoids premature schema lock-in |
| 14 | Streaming excluded from `tool_schemas()`; descriptive `stream_catalog()` | `call_tool` is one-shot; held subscriptions need the agent bus (out of scope) |
| 15 | new `SimStreamBus`, keep `SimI2cBus` non-streaming | preserves the "async behind non-streaming i2c fails at setup" conformance fixture |

---

## 13. Open questions (for ratification)

- **Watchdog initial arming** — arm-on-first-command (chosen) vs arm-at-bind (stricter). Safety-
  relevant; interacts with lazy connection open. Sign-off wanted.
- **safe_state dispatch worker** — does "one timer thread" permit a separate execution worker for
  `safe_state` (yes proposed, to avoid head-of-line blocking)? Confirm the single-thread mandate's
  spirit.
- **Mass-trip / load-shed on resume** — beyond fire-all + log lateness, any throttling for a
  grossly-late timer thread (minutes behind after a stall)?
- **Default numbers** — HWM 256, `queue_max`, cancel/watchdog join 2 s, reconnect budget 5,
  backoff 200 ms…30 s, default setup/idle timeouts — validate against a real high-rate stream
  (CAN/pose) in the sim before locking; expose as documented module defaults.
- **Heartbeat re-arm** — should a deliberate no-op "I'm still here" command be allowed to re-arm
  the watchdog without a real side effect?
- **Idle-policy overridability** — chosen per-call-overridable; confirm against "idle is a device
  property" intuition.
- **A blessed `Streamable`/`StatusSource` capability Protocol** (semver, event-kind vocabulary,
  ordering guarantees) — specify now or defer to the capability-RFC process?
- **`config.stream` home** — leaf device vs bus node vs both (setup_ms is path-ish, idle_ms is
  topic-ish).
- **Lazy `stream()` setup** — defer hop-open to first `__aiter__` to avoid blocking a loop thread
  at construction (F2)?

---

## 14. Out of scope (forward-compatible seams)

- **Stream failover** — `routes:` for subscriptions (commit to one route, on drop → Gap +
  re-subscribe on the next). `_next_route` is a stub; reconnect is same-path only. A
  delivery-unknown write still never auto-failovers.
- **Agent bus wire protocol** — a far-side native SHAL runtime carrying ops + streams; would make
  `stream_catalog()` callable. `multiplexes` and the `ChannelHandle` abstraction are the seams.
- **Hotplug / discovery** — topic enumeration, node lifecycle events, id stability. The opaque
  `topic` contract and `stream_topics` declaration leave room.
- **Sequence-number gap fidelity** — `seq_lo`/`seq_hi` on `Gap` when a future bus exposes them
  (precedence vs timestamp spans TBD).
- **Non-asyncio loops** (Trio/anyio) — a pluggable loop-adapter seam; asyncio is the only target now.
- **`capture()` `max_records` ceiling** — designed, not built this phase.

---

*Produced by reconciling 10 independently-drafted specialist sections against the locked DESIGN V2
decisions and the Phase 1 code, then resolving every contradiction and high-severity gap the
adversarial review surfaced (notably the reader-lock deadlock, the divergent core types, the
setup-rejection taxonomy, the backpressure default, and the watchdog mux-pin safety hole).*
