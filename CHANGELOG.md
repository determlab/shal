---
type: changelog
owner: repo-agent
scope: repo/shal
reviewed: 2026-08-31
---

# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **`check_driver` warns when an `@op` has no `side_effect`** (#162) — the op is
  still legal and still gated (it infers `"actuator"`, fail-closed), so this is a
  warning, never a problem, and `shal check` still exits 0. The warning names the op
  and the fix: `<op>: no side_effect declared; inferred "actuator" (gated). Declare
  side_effect= — "none" for a read, "write" for a benign, reversible change,
  "config" or "actuator" for a gated one.` Every ADK reference, example driver and
  demo already declares its labels, so none of them gains a warning.
- **The ADK reference set gains two: `sonos` and `order_service`** (#152, R7 in
  `adk.md` §3.6 / §3.7) — five references now, each the same four files, unregistered.
  `sonos` (`sonos,speaker`) is the root driver that wraps a library: `kind = None`,
  `soco` imported lazily and only for a real speaker, and `address: sim` builds the
  stand-in in its `sim.py` — its test needs no speaker and no `soco`. It came from
  `examples/demos/sonos/`, whose `sonos_driver.py` now only imports the reference, so
  there is one copy. `order_service` (`acme,order-service`) is a software node under
  `shal,http`: `get_order` is a `GET` through the request envelope (`none`),
  `place_order` a `write` (bounded `qty`) that `cancel_order` undoes, and
  `cancel_order` — which this driver cannot undo — is gated (`actuator`). Its twin is
  a `@msg_sim_model` in its own `sim.py`, registered only when that file is imported,
  so core `sim_msg.py` still registers nothing and `import shal` stays clean. `shal
  check` on both: zero problems, zero warnings. The mesh demo keeps its own
  `acme,*` drivers (a different protocol, served by its real processes).
- **The agent guide shows one real failing `shal check` report, and its fix** (#154,
  R9 in `adk.md` §3.6) — the ADK section of `AGENT_GUIDE.md` gains "A failing check,
  and its fix": `shal check my_driver:MyThing --json` on a driver whose `get_volume`
  has no `@op` line, the JSON report it prints (exit 1), and the one `@op(...)` line
  that clears it. `tests/test_guide_failing_check.py` runs the command on
  `tests/fixtures/adk_check/my_driver.py` and fails if the report differs from the
  guide's, then applies the guide's fix line to a copy and requires exit 0. The
  broken example is test-only: not in the reference set, not registered.
- **`shal,http` reaches read-only REST resources: the request envelope** (#104, S1 in
  `adk.md` §3.7) — a message carrying any of the reserved keys `method`, `path`,
  `query`, `headers`, `json` is sent as that HTTP request to
  `<base>/<addr>/<path>?<query>` and answers `{status, headers, json | text}`. A
  message with none of them is the **plain** shape, unchanged: POSTed as JSON, the
  reply is the parsed body. `None` query params are dropped; `json` on `GET` is a
  `LoadError`; a non-2xx is a `HopError` with the status, sent once, never retried.
  **Credentials live on the bus node**: `config: {headers: {Authorization: "Bearer
  ${TOKEN}"}}` (new `config.headers` schema key), `${ENV}` resolved at load, added
  by the bus to every request, winning over an envelope header of the same name,
  and never forwarded across a redirect. They never appear in the envelope, the
  reply, a log or an error; logs carry `<addr>/<path>` and the status only.
  `shal,sim-msg` answers the same envelopes.
- **CI runs the README Quick Start exactly as printed** (#159) — a new `quickstart`
  job (ubuntu + windows, every PR) builds the wheel, makes a clean venv, and runs
  `dev/quickstart/run_readme.py`, which reads the Quick Start blocks out of
  `README.md` and runs them in order: files saved under the name the README gives,
  each command's output matched to what the README shows (only the drifting reading
  by pattern). The README's `pip install pyshal` installs this commit's wheel
  instead. The seconds from `pip install` to the first successful read go into the
  job summary.
- **The ADK reference set, and `shal docs --list` / `--example`** (#149, R7 in
  `adk.md` §3.6) — `ti,tmp102`, `microchip,mcp23017` and `rigol,dp832` ship inside
  the Authoring Kit at `src/shal/adk/reference/<name>/`, each four files: `driver.py`,
  `sim.py` (its twin, moved out of the bus module), `test_<name>.py`,
  `topology.yaml`. They are guide material, **not registered drivers**: no entry
  point, not imported by `import shal`, absent from `shal.catalog()`. `shal docs
  --list` names them; `shal docs --example <name>` prints the four files and the
  folder they live in. Run one by naming its files (D5):
  `shal probe topology.yaml --drivers driver.py --drivers sim.py`. `shal check` on
  each reports zero problems and zero warnings, live probes included.
- **`shal call` — run one op from the command line; the gate refuses on the first
  screen** (#160, R11 in `adk.md` §3.6) — `shal call <topology> <node> <op>
  [value…] [--json]`. An op labelled `none` or `write` runs and prints its result
  (`--json`: `{ok, tool, device, op, side_effect, result}`, the `call_tool` shape).
  An op labelled `config` or `actuator` is refused from its label *before* it is
  called — nothing is sent, the bus is not even activated — with exit **2** and a
  message that names the label and both ways to approve (`shal mcp <topology>`, or
  `with shal.approver(...)` in Python). There is no `--approve` flag (#56). Values
  map to the op's parameters in order or as `name=value`, converted by the op's
  schema. Exit 1 = the op ran and failed; exit **3** = could not run (usage error,
  unknown device/op, bad value, bad topology) — not argparse's 2, so a refusal is
  never mistaken for a mistake.
- **`shal,sim-sensor` — a device that ships, so a bare install has something to
  read** (#156, R10 in `adk.md` §3.6) — a `TemperatureSensor` under `shal,sim-i2c`,
  its model and class next to the bus in `buses/sim.py`. Its reading drifts, so two
  reads differ (the live value is the model's state), and it has one `config` op,
  `set_target`, which stops at the gate. It wraps no part: it binds only under a
  `shal,sim-i2c` bus and fails the load anywhere else. `ApprovalDenied` now says
  how to approve (`with shal.approver(...)` in Python, or an MCP host via
  `shal mcp`). A test asserts every id `shal.catalog()` lists is `shal,*` (D1),
  except an explicit `KNOWN_VENDOR_IDS` set: the seven drivers #149 removes and
  `nxp,pca9548` (awaiting a ruling). A second test fails if an id in that set no
  longer ships, so the set must shrink with #149.
- **`shal check` — conformance from the command line** (#148, ADK R6) —
  `shal check <compatible|module:Class> [--topology t.yaml] [--json]` runs the
  existing `conformance.check_driver` (what it checks is unchanged). `--json`
  prints the report (`compatible`, `ok`, `problems`, `warnings`, `checked`) on
  stdout; without it, the same report as text. Exit 0 when `problems` is empty
  (warnings never fail), 1 when it is not, 2 when the check cannot run (unknown
  compatible, bad `module:Class`, import failure, missing topology) — a message
  on stderr, never a traceback. `module:Class` imports from the cwd, so an
  unpackaged `driver.py` is `shal check driver:MyThing`; an unregistered class is
  registered for that one process so its catalog entry and `--topology` resolve.
- **`shal.record` — one test-result shape for the bench and the floor** (#141,
  D22, `record.md`) — a new module `src/shal/record.py` holding the record shape
  **once**, so `pytest-shal`, Bricks and AOS stop writing three different things.
  One `Record` per run of one sequence against one unit: `record_version` first
  (§7 — a reader always knows what it holds), then the id, `unit` (`bench` for a
  bench run, never blank), `station`, `sequence`/`sequence_version`, optional
  `firmware`, `setup`/`setup_version`, `runner`, `started`/`ended`, the derived
  `verdict`, `steps[]` (each with its own verdict and its `measurements[]`, every
  measurement carrying the **limits that were enforced**, copied in — a record
  must be readable in a year without the topology), the AOS `calls` list
  unchanged, and an optional `abort` block for when Predictor or a person stopped
  the run early. `verdict` is a read-only property derived from the steps and
  `abort` (`aborted` > `error` > `fail` > `pass`), so it cannot be constructed
  wrong, and a stored record whose `verdict` disagrees with its own steps is a
  `RecordError` rather than a silent correction. `write(record, store)` writes
  **both** stores under one directory — `records.db` (SQLite, one file per
  station, the record as a JSON column plus indexed `unit`/`station`/`sequence`/
  `verdict`/`started` columns) and `records/<id>.yaml` beside it — and `read(store,
  unit=…, station=…, sequence=…, verdict=…)` returns what the **YAML** says: the
  audit copy wins on disagreement, so a stale db row changes neither the data nor
  which records a filter matches. `started`/`ended` are ISO-8601 UTC *strings*,
  not `datetime`, because an unquoted YAML timestamp parses back as a `datetime`
  while JSON has no such type — a `datetime` field could not come back identical
  from both stores; unset optional fields are omitted (as `record.md` §2 itself
  writes `limits: {max: 50}`), key order is the spec's, and a non-finite float or
  a non-plain value inside the pass-through `calls` list is refused, which is what
  makes the two serialisations byte-stable. `RecordError` is its own type under
  `shal.Error` — nothing hopped, and a result read back months later is not the
  topology load path D18 defines `LoadError` for; its messages name the file and
  the offending key and never the value, because `calls[].result` holds whatever a
  driver returned. **No consumer wiring yet**: nothing in `shal` imports it and it
  is deliberately not re-exported from `shal/__init__.py` — `pytest-shal` (R2),
  Bricks/AOS (R3) and Predictor (R4) follow. See `tests/test_record.py`.
- **`include:` composes a topology from many YAML files — add a device, add a
  file** (#134, D21) — a top-level `include:` list names other topology files
  (each a full `root:` of its own); their `root:` maps merge as **siblings**
  into the main file's `root:` — nothing nests. There is no override: a
  duplicate top-level name across any two files is a `LoadError` naming both
  files, so include order never matters (duplicate global `id:` was already a
  `LoadError` and still is). An include may not escape the main file's
  directory tree — the same confinement rule and cycle guard `use:` already
  had, now shared via `_confine`/`_IncludeCtx`. Only the main file's `.env` is
  read; an included file's own sibling `.env` is never consulted. `include:`
  does not take `with:` — a parametrised board still goes under a named node
  with `use:`. Include chains are allowed (a included file may itself
  include). Schema: `root`/`template`/`include` are each independently
  sufficient at the top level, so a main file may be include-only. See
  `tests/test_includes.py`.
- **A `packaging` CI job checks the built wheel's own metadata, not the source
  that declares it** (#123) — `test`/`examples` install `.[dev]` from a repo
  checkout, so the wheel's `Requires-Dist` was never once exercised; that gap
  is exactly how `mcp>=1.0` shipped with no upper bound and broke every fresh
  `pip install "pyshal[mcp]"` for 34 days while CI stayed green (#105/#106).
  `dev/packaging/check_dist.py` builds the wheel, reads its METADATA, and
  fails if (1) a third-party import that runs at module scope in `src/shal`
  (i.e. at `import shal` time) has no matching base dependency declared, or
  (2) any declared dependency (base or extra) has no upper bound and no
  written exemption in the script. A *lazy* import — nested inside a
  function/method, like the `mcp` SDK import in `src/shal/mcp/server.py` or
  the `jsonschema` import in `src/shal/loader.py:_validate_schema` — is
  deliberately exempt from (1): it only breaks the feature that calls it, not
  `import shal`. `pyyaml` and `jsonschema` now carry `<7`/`<5` ceilings, the
  same convention as `mcp>=1.0,<2`; `pytest`/`pytest-cov`/`ruff` (the `dev`
  extra) are exempted with a written reason instead — they're never part of a
  plain `pip install pyshal`, and CI's `test`/`examples` jobs already install
  and exercise them on every PR, so a breaking major there fails loudly right
  away instead of silently for 34 days. The clean-venv, no-extras wheel
  install already lives in the `build` job (installs the wheel, runs
  `shal --help` / `shal docs` / `shal-mcp --help`); `packaging` doesn't repeat
  that — it's a static metadata check and needs no venv of its own.

### Fixed
- **`shal probe` no longer points at a `--probe` flag it does not have** (#166) —
  the footer that lists the writes now reads ``# writes — not run by `shal probe`;
  use `shal call` (gated ops are refused until approved): <tools>``. The legacy
  `shal-mcp --probe` prints the same line: `shal probe` is the front door and
  `shal call` ships in the same package. The two errors of `shal probe <tool>` had
  the same fault and a `shal-mcp:` prefix; they now read ``shal: no tool '<tool>'.
  Run `shal probe <topology>` (no tool) to list what this topology exposes.`` and
  ``shal: `shal probe` runs reads only; '<tool>' changes hardware — use `shal call`
  (gated ops are refused until approved).`` (exit 1, as before). The
  `shal-mcp --probe` flag itself is unchanged.
- **`rigol,dp832` `set_voltage` declares its limits from the DP832 ratings**
  (#150, R8a in `adk.md` §3.6) — `params=` bounds `volts` to 0-30 V (CH1/CH2),
  and `op_limits()` narrows CH3 to 0-5 V (Rigol DP800 Series datasheet). A value
  above the channel's rating is a `LimitError` before any bus I/O, and
  `shal check rigol,dp832` no longer warns.
- **Including a `use:` template file now fails loudly instead of loading
  nothing** (#137, D21) — an included file with `template:` and no `root:`
  (a board meant for `use:`, not a topology) used to pass schema and merge
  zero nodes, so the load reported success while silently reaching nothing —
  exactly the class of bug D21 exists to stop. `_merge_includes` in
  `src/shal/loader.py` now rejects an included file that has neither `root:`
  nor `include:` with `a use: template cannot be include:d — reference it
  from a node`, naming the file. An included file that only chains further
  `include:`s is still legal (#136).
- **A node missing `address`/`routes`/`to` now names the node and the rule**
  (#95) — a cloud device's natural first draft (`id`/`driver`/`config:`, no
  `address`) used to fail with jsonschema's raw *"is not valid under any of
  the given schemas"*, naming neither the node nor what was wrong.
  `_validate_schema` in `src/shal/loader.py` now recognizes this one shape —
  the schema's own address/routes/to `oneOf` failing because the node
  instance has none of the three keys — and raises `<node>: needs exactly one
  of address | routes | to` instead. Detection matches on the failing
  subschema's *shape* (three branches, each `required: [<key>]`), not its
  location, and on the instance itself lacking all three keys, so a node that
  already has an `address` but fails for an unrelated reason (an unknown key,
  or `address` + `routes` both given) keeps its own honest message, and a
  `use:` node — which legitimately has none of the three, inheriting them
  from its template — is unaffected. `src/shal/AGENT_GUIDE.md` now shows a
  cloud-device example with both `config:` and `address`.

### Changed
- **BREAKING: no device driver ships registered** (#149; D1 re-affirmed on #110,
  D7 amended) — the seven bundled drivers and the `nxp,pca9548` mux chip leave the
  package's registry and its `shal.drivers` entry points. `ti,tmp102`,
  `microchip,mcp23017` and `rigol,dp832` become the ADK reference set (above);
  `microchip,mcp9808`, `ti,ads1115`, `ti,ina219` and `keysight,34461a` move to
  `examples/drivers/<name>/` with their sim models and tests (the examples CI job
  runs `pytest examples/drivers`). `nxp,pca9548` moves to `examples/drivers/pca9548/`
  with its sim model; its tests stay in `tests/test_buses.py` and
  `tests/test_sim_sensor.py`, which import it from there. Code moved, not rewritten:
  only import paths changed. The mux **mechanism** — `MuxState`, `MuxChannel` —
  stays in `shal.buses.mux`; only the chip left. The `shal.drivers` package is
  gone. A bare install's `shal.catalog()` now lists only `shal,*` ids, and the D1
  test asserts exactly that — the `KNOWN_VENDOR_IDS` allowlist #156 left is
  deleted. A topology that names one of these ids now needs `--drivers` (or an
  import) to resolve; `shal,sim-sensor` is the device a bare install reads.
- **`microchip,mcp23017` `set_direction` is labelled `config`, not `write`** (#151,
  R8b in `adk.md` §3.6) — turning a pin into an output arms it, so the call now
  stops at the approval gate like any `config` op. `set_direction` and `write_pin`
  now declare `pin` as 0-15, so pin 16+ raises `LimitError` before any bus I/O on
  both ops, and `check_driver` reports no problems and no warnings for the class.
- **The release workflow now fails if the version is already on PyPI** (#116) —
  `release.yml` publishes with `skip-existing: true`, which exists so that re-running a
  partially-failed upload does not explode. The cost is that "uploaded nothing" and
  "uploaded everything" produce the same green check: a release cut against a version
  that is already live — a bad bump, a re-run after a manual upload, a tag pointing at
  the wrong commit — would report success while PyPI kept serving the old wheel. That is
  the same shape as #105/#106, a green signal sitting beside a stale artifact, and it is
  the shape this repo is trying to stop having. A new step between `Build` and `Publish
  to PyPI` reads the version out of the built wheel filename — the wheel is the artifact
  that actually gets uploaded, so it is the thing whose version must not already exist,
  and reading it from the artifact rather than from `pyproject.toml` keeps the check
  honest if the version ever becomes dynamic — queries
  `https://pypi.org/pypi/pyshal/json`, and exits non-zero if that version is already
  released, naming the version and its upload date. A 404 from that endpoint means the
  project has no releases at all, so nothing can collide and the guard proceeds; an
  unreachable PyPI fails the release instead, because a release wrongly blocked costs one
  re-run of the job while a release wrongly reported green costs a month of nobody
  noticing. `skip-existing: true` is deliberately kept: the guard is what makes it safe,
  so it now only ever covers a retried upload of a genuinely new version. Both the step
  and the `skip-existing` line carry comments saying so, because the two look redundant
  side by side and are not.
- **CI runs on a schedule, and a canary job installs dependencies unpinned** (#106) —
  `ci.yml` fired only on `push: [main]` and `pull_request`, so 40 days without a commit
  meant 40 days without a single CI run: the `mcp` 2.x break landed 2026-07-28 and
  nothing executed to catch it, leaving a stale green badge over a broken
  `pip install "pyshal[mcp]"` for 34 days (#105). The workflow now also runs weekly
  (`cron: 17 6 * * 1` — off the hour and off midnight UTC, where GitHub's scheduler is
  most congested) and on `workflow_dispatch`. A new `latest-deps` job installs the
  package with `--no-deps` and then `pip install --upgrade`s `pyyaml`, `jsonschema` and
  `mcp` unpinned, so it deliberately resolves past the declared ceilings (`mcp>=1.0,<2`
  from #107) that the normal `test` job can never see past; it then asserts `import mcp`
  hard (the suite's `pytest.importorskip("mcp")` would otherwise *skip* into a green
  canary), builds a real MCP server via `_build_server` — importing `shal.mcp.server`
  proves nothing, since the SDK import is lazily inside that function, and #105's
  `AttributeError: 'Server' object has no attribute 'list_tools'` only fires when the
  handlers are registered — and runs the suite, so the next upstream major fails our
  build instead of only failing users. The job is additive and not a required check — an upstream release is news, not
  a broken PR — and it is gated to scheduled/manual runs, so it never marks an unrelated
  PR red. It carries no `continue-on-error`: a red canary must actually be red, or nobody
  looks. Note that GitHub disables scheduled workflows after 60 days of repository
  inactivity (it emails the owner first), so on a quiet repo the fix for silent decay can
  itself be switched off — re-enable it from the Actions tab if that mail arrives.
- **`hard_stops.protected_paths` now fences the files that actually hold the invariants**
  (#118) — four of the six inherited globs matched no tracked file (`**/auth/**`,
  `**/security/**`, `**/migrations/**`, `**/redact*`), so the redaction sanitizer, the
  gate's enforcement path and the `${ENV_VAR}` resolver were all editable by an
  `agent:go` run with no hard stop. The dead globs are gone; `approval.py`, `driver.py`,
  `limits.py`, `log.py`, `loader.py` and `mcp/bridge.py` are fenced by exact path, each
  with an inline comment naming the invariant it protects, and `.agent-loop.yml` fences
  itself so an agent cannot rewrite the config that governs it. Every entry is verified
  to match at least one tracked file; the new set is a strict superset of the old one.
- **Every document now declares a `type` and an `owner`** (#119) — `doc-standard.md` v1.0
  front-matter (`type`/`owner`/`scope`/`reviewed`) on all 55 markdown files; `ops`'
  `doc-check.py` passes. `AGENTS.md` moved to `docs/agents/context.md` (the standard's
  working-context path); `.agent-loop.yml` `review.standards_sources` follows it.
- **One decision ledger, not two** (#119) — `docs/ARCHITECTURE.md` §5 is the ledger of
  record. The still-load-bearing items of the v2.1 addendum were lifted into it as
  **D17–D20** (validation split, error taxonomy, the single `shal.drivers` entry-point
  group + bind-time wrapping, `provide_child_bus`); `DECISIONS - V2.1.md` moved to
  `docs/design/archive/` marked superseded. `docs/agents/domain.md` now names the same
  ledger and drops the never-used `docs/adr/` instruction.
- **`shal docs` strips the front-matter** (#119) — `SDK.md` and `AGENT_GUIDE.md` ship in
  the wheel and are typed like every other document, but the header is repo bookkeeping,
  so the printed guide is unchanged for an agent reading it.

### Fixed
- **Every remaining bus address echo from #101 now routes through `redact_url`**
  (#126) — thirteen `LoadError`/`HopError` sites across `src/shal/buses/`
  interpolate an address; a first pass redacted only two, on the premise that a
  bus's *own* address is `${ENV}`-resolved while a *child* address is validated
  down to a narrow non-credential type (an int, a label). That premise was
  false: `loader.py` resolves `${ENV}` for every node, children included, so a
  child address is just as capable of holding an arbitrary string — reproduced
  live via a mux child address holding a creds URL that echoed verbatim in a
  `LoadError`. All ten remaining sites (`i2c_cli.py`, `mux.py`, `scpi_raw.py`,
  `sim.py`, `sim_msg.py`, `sim_scpi.py`) now redact too. This costs nothing for
  legitimate errors: `redact_url` only rewrites a value containing `://` or
  `@`, so an int, a device path, or a plain label passes through
  byte-identical. `drivers/rigol_dp832.py:26` stays out of scope (a SCPI
  channel label, not a credential).
- **Every `shal` command picks the Windows selector event loop, not just `shal mcp`**
  (#94) — on win32 a driver that wraps an `aiomqtt`-style library runs its own asyncio
  loop, and the default `ProactorEventLoop` has no `add_reader`, so that loop dies with
  `NotImplementedError`. `shal mcp` set `WindowsSelectorEventLoopPolicy` for itself
  (#87); `shal probe` went straight to `bridge.call` and did not, so the same driver
  served fine over MCP and failed under the CLI a cold user is told to try first. The
  choice now lives in one helper (`shal.cli._use_selector_loop_on_win32`), called once
  at the top of `shal.cli.main` — so `probe`, `tools`, `mcp` and every subcommand added
  later are at parity — and at the top of `shal.mcp.server.main`, which the legacy
  `shal-mcp` script enters directly; `server.py`'s own inline copy is gone, leaving one
  mechanism. It is set by the *commands*, never at import: a host app that merely
  imports `shal` keeps its own event-loop policy, the same rule as "the library never
  configures logging".
- **The bind log no longer leaks credentials** (#117) — `loader.py` wrote
  `str(node.address)` straight into the DEBUG `bind` record's `addr` field, so an
  address resolved from `${ENV_VAR}` carrying `user:pass@` or a token query string was
  written in plaintext to whatever handler the host app attached, on every *successful*
  load. The address now routes through `redact_url` like every other address that
  reaches a log or an error. Clean addresses still log unredacted.
- **Load-time `LoadError` redacts credential-bearing addresses** (#101) — a malformed
  `http(s)://user:pass@...` address (e.g. resolved from `${ENV}`) no longer echoes
  userinfo credentials in the error text; the `http`, `tcp`, and `scpi-raw` buses now
  route the echoed address through `redact_url`. Clean addresses still echo verbatim.

### Documentation
- **Three ledger rows: D23–D25** (#158, `adk.md` §3.6 R10 / §3.7) — what ships
  (`shal,*` only; D1's line made explicit, and D1's Source now points at D23), the
  side-effect rule for software (with the restorable-delete ruling), and the HTTP
  request envelope. No code changed.
- **Name the ADK** (#140, `adk.md` R1) — a new "## The ADK" section in
  `AGENT_GUIDE.md` names the five pieces an agent already uses to build a
  compliant driver: the rules in this guide, `@op` in `driver.py`, the typed
  protocols in `capabilities.py`, `conformance.check_driver`, and the sim-twin
  buses (`buses/sim.py`, `sim_msg.py`, `sim_scpi.py`). No code changed. Whether
  the ADK lets a cold agent pass on the first try is a separate, scripted test
  in `determlab/adk-lab`; this entry does not claim that result — only a real
  `RESULTS.jsonl` there can.

## [0.2.2] - 2026-08-31

Restores `shal mcp` on a clean install, and makes the D12 read-freshness contract
enforced rather than merely documented. Both found by the 2026-08-30 Decision Ledger
audit (`decision-ledger-standard.md` v1.0) — see #107 and #108.

### Changed
- **`i2c-cli` short/empty read now raises `HopError`, not `IndexError`** (#108) — on
  exit 0, `i2ctransfer` returning fewer bytes than the `Read` ops requested (including
  a bare empty stdout) used to fall through as `parse_output(b"")` -> `b""` with no
  raise; a downstream driver (e.g. `tmp102`) then indexed the empty result and died
  with a bare `IndexError`, losing all `path`/`hop`/`delivered=` context. `i2c-cli.txn`
  now raises `HopError(delivered="unknown")` on a short read, matching `spi-cli`'s
  wording and style — read freshness (D12) is enforced for real, not just documented.

  *Upgrading — this is a breaking change.* An `i2c-cli` short or empty read used to
  return empty bytes; it now raises `HopError`. Any code that caught the old shape, or
  that relied on the read returning at all, breaks. Pre-1.0 permits the change and the
  old behaviour was wrong — it discarded `path`/`hop`/`delivered=` and resurfaced as an
  unrelated `IndexError` a layer up — but callers must update, not merely reinstall.

### Fixed
- **`conformance` now probes read freshness (D12)** (#108) — `check_driver()`'s live
  checks gained `_probe_freshness`: for a device driver bound behind a `shal,sim-*`
  bus (which ship a `fail_delivered_unknown` hook), it forces one non-delivering hop
  and requires a zero-arg read op to raise `HopError` rather than return a stale
  value. Silent where it can't observe (a driver wrapping a third-party client —
  SDK.md 1b/108 is explicit conformance can't see inside that), so it never fakes
  coverage it doesn't have.
- **Pin the MCP SDK major** (#107) — the `mcp` extra was `mcp>=1.0`, unbounded, so a clean
  `pip install "pyshal[mcp]"` could resolve MCP 2.x, whose low-level API dropped the
  `@server.list_tools()` / `@server.call_tool()` decorators `src/shal/mcp/server.py` is
  written against, breaking `shal mcp` at import with `AttributeError: 'Server' object has
  no attribute 'list_tools'`. Both the `mcp` and `dev` extras now pin `mcp>=1.0,<2`.

## [0.2.1] - 2026-06-23

Cold-user blockers found by the 0.2.1 cold-install verification (run 1) — see #88.

### Fixed
- **Version single-source bumped to 0.2.1** (#81) — `pyproject` + `shal.__version__`.
- **`.env` inline `# comments`** (#86) — an unquoted ` # comment` is now dropped, so
  `HOST=h.example # prod` resolves to `h.example` instead of leaking the comment into a
  cryptic `getaddrinfo` failure. `#` inside quotes or with no leading space stays literal.
- **`--drivers _file.py`** (#85) — a driver file named explicitly on the command line is
  now imported even if it starts with `_`; only directory scans skip `_`-prefixed files.
  No more silent "no driver installed".
- **MCP first-read warm-up** (#83) — `shal mcp` now calls `Hal.warm()` to eagerly
  activate transports before serving, so the first MCP read no longer hangs through a
  lazy connect/login before warming. Best-effort: a bus that can't come up is a friendly
  stderr warning, not a dead server.
- **Windows aiomqtt event-loop policy** (#87) — `shal mcp` selects
  `WindowsSelectorEventLoopPolicy` on win32, since the default `ProactorEventLoop` lacks
  `add_reader` and breaks aiomqtt-based drivers (e.g. Deebot). The guide documents the
  one-liner for users' own scripts.
- **`shal mcp` dispatches driver ops off the event loop** (#92) — a driver wrapping an
  async library (e.g. `deebot-client`) that read fine under `shal probe` (sync) used to
  fail on its first call under `shal mcp` with *"Cannot run the event loop while another
  loop is running"*. The bridge now runs each op via `anyio.to_thread.run_sync`, so
  async-library drivers work over MCP and a slow op can't freeze the server. The shipped
  guide gains a "wrapping an async library" recipe (persistent loop on a dedicated thread).
  Found by the #88 cold-user live-MCP run.

### Documentation
- **Install `pyshal`, import `shal`** (#82) — the in-package guide states the
  distribution/module name gap up front so a cold user never hits `ModuleNotFoundError`.
- **Device-library Python floor** (#84) — the wrap-a-library recipe notes that a device's
  own library may need newer Python than SHAL core (e.g. `deebot-client` → 3.11+).
- **Full SDK ships in the wheel** — the complete Driver & Bus authoring contract moved
  from `docs/SDK.md` into the package (`src/shal/SDK.md`); print it offline with
  `shal docs --sdk`. A pip-only agent authoring a bus or a hardware (I2C/SPI/SCPI) driver
  no longer needs GitHub. `AGENT_GUIDE.md` remains the cold-user quickstart slice; D16 now
  names the in-package SDK as the contract source.
- **Release-doc hardening** (#99, CMO docs-gap audit) — user-facing docs now flag the
  device-library Python floor (the Deebot path needs 3.11+ — `asyncio.TaskGroup`) in
  README / CONNECT / AGENTS / CONTRIBUTING / SDK; the MCP-host registration and the sonos
  demo use the shipped `shal mcp` front door instead of the legacy `shal-mcp` alias;
  `ARCHITECTURE.md` §3 reflects the shipped `shal probe/tools/mcp/docs` verbs; the agent
  issue-tracker doc points at `determlab/shal`; and the in-package guide notes multi-device
  tool handles (`<id>_2__op`) and the `--drivers` `_`-prefixed skip.

## [0.2.0] - 2026-06-22

### Added
- **In-package agent guide + `shal docs`** (#55) — a provider-neutral "add a device"
  guide now ships **inside the wheel** (`shal/AGENT_GUIDE.md`); `shal docs` prints it. A
  pip-only agent can author a working wrap-a-library driver — root driver, `@op`
  side-effects, the read-freshness rule, load with `--drivers`, read with `shal probe` —
  with no GitHub and no source-diving. The demo slice of the full Authoring Kit
  (#22/#23/#24). Per `docs/ARCHITECTURE.md` D7 / Principle 3 (self-sufficient from the package).
- **`shal` CLI — the base front door** (#54) — `shal probe <topology>` prints a real
  device reading and exits, `shal tools` lists the device tools (read / gated), and
  `shal mcp` serves to an MCP host (the adapter); `--drivers` loads local drivers. The
  legacy `shal-mcp` command is now an alias of `shal mcp`. SHAL stands on its own
  without MCP — the read path no longer hides under a host-named command. Per
  `docs/ARCHITECTURE.md` D11.
- **MCP server — the agent-host front door** (#25/#26/#27) — `shal-mcp <topology.yaml>`
  serves a SHAL topology to any MCP host (Claude Code/Desktop, …) as typed, gated
  tools. Reads run free; a state-changing op is **never executed on first call** —
  it returns an `approval_required` ticket that a human authorizes via the separate,
  destructive-flagged `shal_approve` tool (host-agnostic in-band approval). `--approve
  auto` (or `SHAL_APPROVE=auto`) opts into free writes and records the choice in the
  audit log. The `mcp` SDK is an optional extra (`pip install pyshal[mcp]`); the core
  stays at two dependencies. The SHAL→MCP mapping lives in a dependency-free
  `shal.mcp.Bridge` (fully unit-tested without the SDK).
- **`shal-mcp --drivers`** (#47) — load local/unpackaged driver modules before
  serving: `shal-mcp my.yaml --drivers ./drivers/` imports a `.py` file or a whole
  directory (repeatable) so each driver's `@shal.register` runs. Makes
  bring-your-own-driver setups runnable from the CLI without packaging every driver,
  while the topology YAML stays pure data (imports are operator-controlled on the
  command line). An unresolvable `compatible` now points at the flag.
- **`shal-mcp --probe`** (#39) — a one-shot, human-runnable read: `shal-mcp my.yaml
  --probe` prints every device's current readings and exits; `--probe <tool>` runs
  one named read. **No MCP host required**, and (like the Bridge) it needs no `mcp`
  extra. Reads only — writes are listed but never run. The "install → see a real
  value in one command" path, instead of dead-ending at a stdio server.
- **`MediaPlayer` capability + a Sonos example driver** (#28) — a new `MediaPlayer`
  capability (play / pause / stop / next / previous / volume as benign writes;
  now-playing / state / volume as free reads). The `sonos,speaker` driver that
  implements it — the canonical "wrap an existing Python library" root driver
  (`kind=None`, wraps `soco`, sim-first) — ships as a **repo example**
  (`examples/demos/sonos/`), **not** bundled in the installed package, keeping the
  core device-agnostic (the front door points at a topology YAML; devices are
  examples/community packages).
- **`shal.load()` accepts an in-memory topology dict** (#29) — not just a file
  path — the shape a programmatic setup flow builds.
- **Approval-ticket hardening — a "no" is first-class and final** (#36) — the MCP
  bridge gains a `shal_deny` tool that discards a pending action; because the
  ticket is consumed on either decision, a denied (or already-run) `approval_id`
  can never be replayed as an approval. Every ticket transition — `requested`,
  `approved`, `denied` — is now written to `shal.audit` correlated by the
  `approval_id`, so a refusal is exactly as visible as a successful action. The
  approval stays bound to the `(tool, arguments)` the human saw — args smuggled
  into the confirm call are ignored — and pending tickets are in-memory only, so a
  restart fails closed. Regression-tested.

### Changed
- **CI hardened** (#6) — a **wheel-smoke** job installs the built wheel into a clean
  venv and runs the `shal` / `shal-mcp` CLIs + `shal docs`, and asserts the in-package
  `AGENT_GUIDE.md` actually ships (guards entry points + package data). The test run now
  reports coverage (`--cov`, currently ~89%).

### Fixed
- **One gate, not two** (#52) — the MCP `Bridge` no longer ran a *parallel* gating
  mechanism that bypassed the framework's op-layer gate (it installed `AutoApprove`
  and re-gated on `destructiveHint`). It now installs a **deferring** Approver and
  runs the op through the **single** op-layer gate: a gated op defers pre-I/O
  (nothing sent) and is rendered as the `approval_required` ticket. One enforcer →
  advertised == enforced, and an ambient approver can't silently disable the gate.
  Per `docs/ARCHITECTURE.md` D4; adversarially tested (a gated write can't reach the
  device ungated on either call path).
- **Unsupported device is never a dead end** (#42) — when a topology names a
  `compatible` no driver provides, the error now **signposts both ways forward**:
  load a driver you already have (`--drivers`), or — for a device SHAL doesn't support
  yet — wrap its Python library as a driver (run `shal docs`) and load it. Per
  `docs/ARCHITECTURE.md` (the front door points at the add-a-device path).
- **Reads must be live, not a stale default** (#53) — documented the read-freshness
  contract in the SDK (`docs/SDK.md` §1b): a read returns a value **only if the device
  answered this call**, otherwise it raises `shal.HopError` — never a cached / seeded /
  default value dressed up as live. Guards the "trust what the agent reads" promise,
  especially when wrapping a third-party library that returns a default before the
  device responds (the framework can't police that — the driver author must). Per
  `docs/ARCHITECTURE.md` D12.
- **Docs reachable for `pip` users** (#40) — README links were repo-relative, so
  they 404'd on PyPI and for anyone who only `pip install`ed. They're now absolute
  GitHub URLs. The **driver-authoring guide** (`docs/SDK.md`) — previously unlinked —
  is now surfaced in the README's Documentation section, and `shal-mcp --help` points
  at the docs + the SDK guide.
- **Approval gate fail-open** (#19) — an un-annotated, non-idempotent op on a
  device driver is now inferred **fail-closed** as `"actuator"` (gated) instead of
  `"write"` (ungated), so a forgotten `side_effect` stops for approval rather than
  silently reaching hardware. Reads (`@idempotent`) stay ungated, and an explicit
  `side_effect="write"` remains a benign, ungated state change. Makes the README's
  "asks before it moves … unbypassable" claim true by default. Regression-tested.
- **Secret leak in logs/errors** (#20) — credentials carried in an address
  (`https://user:pass@host`, or userinfo on a `host:port`) and URL query strings
  no longer reach `HopError` text or bus logs. A single `redact_url()` sanitizer
  strips userinfo + query/fragment, keeping the bare `scheme://host[:port]/path`
  endpoint (operational context, not a secret). Applied uniformly to the `http`,
  `tcp`, and `scpi-raw` buses. Regression-tested.

## [0.1.0] - 2026-06-15

First PyPI release ([`pyshal`](https://pypi.org/project/pyshal/) — import name `shal`).
The Phase 1 synchronous core plus the driver/instrument, conformance/SDK,
declared-limits, and human-in-the-loop approval work that landed before publishing.

### Fixed
- **Authoring-contract drift** (#15) — aligned the `shal-build-*` skills with
  `docs/SDK.md` and the framework so a driver copied verbatim from the
  `shal-build-driver` skeleton passes `conformance.check_driver` (now regression-
  tested): the skeleton uses the blessed `shal.TemperatureSensor` and includes the
  required `llm_ready` + `@op` (no longer framed as "optional"). Also fixed the
  `src/shal/schema/` path in `shal-build-yaml`, completed its bundled-id list
  (added `shal,scpi-raw`/`shal,sim-scpi`/`shal,sim-msg`, pointing at
  `shal.catalog()` as authoritative), added `txn=` to the documented `HopError`
  signature, and documented the actuator `safe_state()` hook in the SDK.

### Added
- **Human-in-the-loop actuation gate** (#14) — actuator and destructive/config
  ops (`@shal.op(side_effect="actuator"|"config")`) now stop for an injectable
  `Approver` *after* the limit check and *before* any bus I/O. The gate lives in the capability-wrapper, so neither the
  tool surface (`call_tool`) nor the raw path (`get_device().method()`) can bypass
  it. SHAL ships the mechanism + a safe default (`ConsoleApprover`: prompt when
  interactive, deny when headless) plus `AutoApprove`/`DenyAll`/`CallableApprover`;
  install one with `shal.set_approver(...)` or the `shal.approver(...)` context
  manager. Refusal raises `shal.ApprovalDenied` (nothing sent) and `call_tool`
  returns `{"ok": False, "rejected": "approval"}`. Every decision (approved/denied)
  is written to `shal.audit`. Order is always limits → approval → I/O.
- **Declared operating limits** (#10) — `@shal.op(params=...)` takes JSON-Schema
  fragments per parameter; the merged schema is advertised verbatim in
  `tool_schemas()`/`catalog()` AND enforced by the framework before any bus I/O
  (`shal.LimitError`; rejected writes are audited `outcome=rejected`). Two
  narrow-only layers stack on top: `driver.op_limits()` for address-dependent
  ratings and YAML `config.limits` for installation policy — widening fails the
  load naming both numbers.
- **Conformance kit** (#10) — `shal.conformance.check_driver()` self-certifies a
  driver: static checks (llm_ready, @op metadata, schema well-formedness) plus
  live probes on a sim topology (limits actually reject pre-I/O, writes actually
  hit the audit channel, capabilities actually isinstance).
- **Generic sim buses** (#10) — `shal,sim-scpi` (`@scpi_sim_model`) and
  `shal,sim-msg` (`@msg_sim_model`) mirror sim-i2c's model registry for the
  MessageTransport families; ships a DP832 model for hermetic SCPI coverage.
- **Driver SDK guide** (#10) — `docs/SDK.md`: the complete authoring contract
  (driver anatomy, capabilities, transport dialects, limits, sims, conformance);
  with the skills, writing a driver requires reading zero SHAL internals. New
  `shal-generate-driver` skill: the documentation→driver generation recipe.
- **Core I²C drivers** (#2/#3) — `microchip,mcp9808` (`TemperatureSensor`),
  `ti,ads1115` (new `ADC` capability), `microchip,mcp23017` (new `GPIOExpander`
  capability). All dependency-free, sim-backed (`shal,sim-i2c` models), hermetic tests.
- **SCPI instrument stack** (#2, Wave 1) — `shal,scpi-raw` bus (SCPI over a raw TCP
  socket, the lab :5025 convention; stdlib sockets only, no VISA; plaintext with a
  required `insecure: true`), plus the first instrument drivers `rigol,dp832`
  (`PowerSupply`) and `keysight,34461a` (`DigitalMultimeter`) and their capability
  Protocols. End-to-end tested against a fake SCPI socket server (no hardware).
- **Driver `ti,ina219`** (#2, Wave 1) — I²C bus-voltage / current / power monitor,
  the first `PowerMonitor` capability. Dependency-free, sim-backed (`shal,sim-i2c`
  gains an `ina219` model), fully hermetic tests.
- **Node-level agent metadata** (#1) — optional `description:` (instance context
  blended into each tool's description, so an agent distinguishes like devices) and
  `expose: false` (omit a node from `tool_schemas()`/`tool_catalog()`/`call_tool()`
  while keeping it usable from Python) on any topology node. Additive; existing
  topologies are unaffected.
- **`shal.catalog()` authoring surface** (#1) — an introspection view of every
  registered driver/bus so an LLM (or a human) can construct a valid topology:
  `catalog()` returns compact summaries, `catalog(compatible)` the full detail.
  Most fields are derived (compatible, required parent kind, capability Protocol,
  ops); a class declares only the irreducible bits via an optional `authoring_meta()`
  classmethod (`address_schema` / `config_schema` / `child_address_schema` as
  JSON-Schema fragments). Op annotations map side-effects to MCP-style hint names
  (`readOnlyHint`/`idempotentHint`/`destructiveHint`), also added to `tool_catalog()`.
- **Topology includes** — a node may `use:` an external `template:` file to graft
  a reusable subtree (a board, a rack) without copy-paste, with `with:` parameter
  substitution (`${param}`), use-site key overrides, include chains, a cycle
  guard, and path confinement to the project tree. Still `yaml.safe_load` only —
  the splice happens in the loader, never via a YAML tag.
- **Registry collision policy** — two different classes claiming one `compatible`
  no longer silently overwrite (last-write-wins). The clash fails the load,
  naming each providing distribution; disambiguate with a node `from:` key,
  `register(..., override=True)`, or by uninstalling one. Re-registering the same
  class stays an idempotent no-op.
- **LLM tool surface** — `@shal.op(description, unit, side_effect)` metadata on
  capability ops; `Driver.llm_ready = True` enforces it at bind time.
  `hal.tool_schemas()` emits Anthropic tool-use definitions for every device op,
  `hal.tool_catalog()` reports per-op `side_effect`/idempotency for gating, and
  `hal.call_tool(name, args)` dispatches — a delivery-unknown write is reported,
  never silently retried. Buses are excluded (they provide transport, not
  capabilities).

- Topology loader: versioned YAML (`shal_version: 1`) with JSON Schema
  validation, global id uniqueness, address-grammar validation at load,
  `$ref` back-links, `${ENV_VAR}` resolution for addresses and `config:` values.
- Typed transport kinds: `ByteTransport`, `CommandTransport` (argv only),
  `MessageTransport`, `Stream` (Phase 2 placeholder); `kinds()` introspection.
- Driver model: registry keyed by `compatible`, entry-point group
  `shal.drivers`, `@shal.idempotent`, framework-owned retry
  (reconnect once / retry once for idempotent ops; delivery-unknown writes are
  never re-fired).
- Bundled buses: `shal,sim-i2c`, `shal,local`, `shal,ssh-host`,
  `shal,i2c-cli`, `shal,spi-cli`, `shal,tcp` (TLS default), `shal,http`,
  `nxp,pca9548` mux with per-mux selection cache.
- Bundled driver: `ti,tmp102` (`TemperatureSensor` capability).
- Lookup API: `shal.load()` context manager, `get_device()` by id/path with
  positional shorthand, deterministic leaf→root teardown.
- Error taxonomy: `LoadError`, `HopError` (`delivered: no|unknown`),
  `HopTimeout`, `Busy`, `Gap`.
- Observability: structured records with stable `event` keys and
  `path/hop/addr/txn/duration_ms` fields on every hop; WARNING on handled
  retries; DEBUG breadcrumbs before raising; `shal.audit` channel for
  actuator-style write ops (silent by default); `shal.logging` with
  `ConsoleFormatter`, `JSONFormatter`, and the `capture()` JSON-lines flight
  recorder.
- Packaging: PEP 621 metadata, `py.typed`, MIT license, CI workflow.
