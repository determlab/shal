---
type: agent-context
owner: repo-agent
scope: repo/shal
reviewed: 2026-08-31
---

# Agent context — SHAL

> **Role charter: `ops/agents/repo-agent.md`** in the `determlab/ops` repo — a
> sibling repo, so that is a path, not a link. It defines what the repo-agent
> role is in every repo and what it may never do. This file is the
> shal-specific half; the charter is the half that is the same in shal, bricks,
> aos and agora.
>
> The one rule worth restating here, because the rest rests on it: **only the
> session with the code may claim something is true in the code.** A sentence
> like *"`hal.py:217` reads `_GATED_EFFECTS`"* cannot be written from a
> distance — someone has to open the file. That is why this repo's ledger,
> changelog, contributor docs and `docs/agents/*` are written here and by no
> other role.

## Overview
SHAL (System/Software Hardware Abstraction Layer) is a Python library for
describing a hardware/software setup in YAML and controlling it from Python —
device-tree-inspired, but dynamic, user-space, and network-capable. The core
idea: **a bus is just a node that provides a transport to its children**, so the
tree is recursive (SSH → I2C controller → sensor has the same shape as cloud →
robot). Phase 1 ships the synchronous core; async/streaming, watchdog, and route
failover are Phase 2.

## Setup
```
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"            # deps: pyyaml, jsonschema; dev: pytest, ruff
```
Python 3.10+ required for SHAL core; a device's own library may need newer (the Deebot
example needs 3.11+ — `asyncio.TaskGroup`). Distributed on PyPI as **`pyshal`**; the import
name and CLI namespace are **`shal`** (`pip install pyshal`, then `import shal`).

## Commands
| | |
|---|---|
| Test all | `pytest` |
| Test one | `pytest tests/test_shal.py::test_load_and_read` |
| Lint | `ruff check src tests` |
| Auto-fix lint | `ruff check src tests --fix` |
| Build | `python -m build` (sdist + wheel) |
| Sim demo | `python examples/demos/mesh/demo_mesh.py` (microservice mesh) |
| | `python examples/demos/deebot/demo_sim.py` (simulated robot vacuum) |

The `shal` CLI is the front door (`shal probe / tools / call / check / mcp`); `shal-mcp` is the
legacy alias of `shal mcp`. There is **no mypy gate** — the code is fully
type-hinted and mypy-clean by intent, but mypy is not in CI; don't reference it as
if it runs.

## Architecture

> **North star — read first: [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md).** It holds the
> high-level component model (the two faces: *Run* / *Author*), the key flows, the user/API
> interfaces, and the **Decision Ledger (D1–D20)**. **Every non-trivial change MUST be
> consistent with it — or amend the Ledger in the same PR.** Don't drift from it silently;
> if a change fights the doc, change the doc on purpose.

- `src/shal/loader.py` — YAML topology loader (safe_load, schema validation, env
  resolution, `use:` templates, `include:` composition, `$ref` links). `use:`
  splices one external template **into a node** (parametrised with `with:`);
  `include:` (a top-level list of relative paths) merges other files' `root:`
  maps as **siblings** into this file's `root:` — nothing nests, no override
  (a duplicate top-level name across files is a `LoadError` naming both), and
  only the main file's `.env` is read (D21, shal#134)
- `src/shal/transport.py` — `Transport` base + typed kind mixins (`ByteTransport`,
  `CommandTransport`, `MessageTransport`, `Stream`)
- `src/shal/registry.py` — driver registry keyed by `compatible`, collision policy
- `src/shal/driver.py` — `Driver` base, `@idempotent`, `@op`, bind-time wrapping
- `src/shal/hal.py` — lookup API, lifecycle, LLM tool surface (`tool_schemas`/`call_tool`)
- `src/shal/node.py` `errors.py` `log.py` `logging.py` `capabilities.py`
- `src/shal/buses/` — `sim`, `local`, `ssh`, `i2c_cli`, `spi_cli`, `tcp`, `http_bus`, `mux`
  (`mux` is the mechanism only — `MuxState`/`MuxChannel`; the PCA9548 chip is an example)
- `src/shal/adk/reference/` — the ADK reference set: `tmp102`, `mcp23017`, `rigol_dp832`,
  each `driver.py` + `sim.py` + `test_<name>.py` + `topology.yaml`; and `sqlite`
  (`sqlite,database`, #157), a root driver over stdlib `sqlite3` whose twin is its
  address (`":memory:"`), so it has no `sim.py`. **Package data, not
  registered drivers** (D1, D7; #149): no entry point, not imported by `import shal`,
  absent from `catalog()`. `shal docs --list` / `shal docs --example <name>` print them.
  The core suite imports them in `tests/conftest.py`; each reference's own test runs in
  its own process (`tests/test_adk_reference.py`)
- **No device driver ships registered.** `catalog()` on a bare install lists only `shal,*`
  (enforced by `tests/test_sim_sensor.py`)
- `src/shal/schema/shal-v1.schema.json` — the canonical topology schema
- `tests/` — pytest suite (mirrors `src/` concerns)
- `examples/demos/` — runnable showcases (Deebot cloud, microservice mesh); **not shipped**
- `examples/drivers/` — the demoted vendor drivers (`mcp9808`, `ads1115`, `ina219`,
  `keysight_34461a`) with their sim models and tests, run in CI's `examples` job
  (`pytest examples/drivers`); and the `pca9548` mux chip with its sim model, whose
  tests stay in `tests/test_buses.py` / `tests/test_sim_sensor.py` and import it
  from there. **Not shipped**
- `examples/driver-creator/` — the doc→driver generation benchmark; **not shipped**
- `docs/design/` — `DESIGN V2.md` (architecture detail); superseded design docs live in
  `docs/design/archive/`

Before any core change: read **`docs/ARCHITECTURE.md` first** — §5 is the **Decision
Ledger of record** (the only one), then `docs/design/DESIGN V2.md` for the detail. The
ledger is **locked** — don't re-litigate a row in a PR; if one genuinely needs to change,
amend it in the same PR.

## Conventions
- Python 3.10+, type hints everywhere, docstrings on public APIs
- Module docstrings state the file's invariants explicitly — read them; they are
  the contract, and tests enforce them
- `ruff` for format/lint (enforced in CI: `ruff check src tests`)
- Imports: stdlib → third-party → local
- Tests in `tests/`; every change ships with tests and keeps the suite green
- Match the surrounding code's idiom, comment density, and naming
- Keep core dependencies minimal (pyyaml + jsonschema only) — don't add deps lightly

## Extending (the common case)
Don't edit the core to add a device or link. Publish a driver/bus via the
`shal.drivers` entry point (the `shal,*` buses are wired the same way in
`pyproject.toml`; no `vendor,part` driver is). Start from an ADK reference
(`shal docs --example <name>`). The agent-agnostic authoring contract is `src/shal/SDK.md` + the
shipped `shal docs` guide (`AGENT_GUIDE.md`). Step-by-step **Claude Code** skills —
one host's rendering of that contract — live in `integrations/claude-code/skills/`:
`shal-build-yaml`, `shal-build-bus`, `shal-build-driver`.

## Keep the skills in sync (required)
The build skills in `integrations/claude-code/skills/` render the public authoring
contract (`src/shal/SDK.md`) for one host — agents and
contributors follow them to author drivers, buses, and topologies. **Any code change
that affects how something is authored MUST update the relevant skill in the same
PR/issue that ships the change.** A skill that lags shipped code is a bug, not a
follow-up. This covers, at least:
- a new/changed YAML node key (also update the JSON Schema + loader) → `shal-build-yaml`
- a transport-kind change, registration/registry behavior, address grammar, locking,
  or error/retry semantics → `shal-build-bus`
- `@op`/capability/`@idempotent` metadata, the LLM tool surface, or the authoring
  `catalog()` surface → `shal-build-driver` (and `shal-build-bus` if buses are affected)

Add "update the relevant `integrations/claude-code/skills/` guide" to the acceptance criteria of any
such issue. Document only **shipped** behavior — flag not-yet-built API as proposed.

## The non-negotiables (security & safety — locked)
These are invariants, not preferences. A change that violates one is wrong:
- **`yaml.safe_load` only** — never construct arbitrary objects from a topology file
- **`CommandTransport` carries argv vectors, never shell strings** — no `sh -c`
- **A delivery-unknown write is never auto-retried** — only `@idempotent` ops retry
  (reconnect once / retry once); the user decides on unknown delivery
- **Per-mux selection state** lives on the mux's shared state object, never the parent bus
- **`kinds()` introspection, never `hasattr`** — forwarding buses delegate explicitly
- **The library never configures logging** — one `NullHandler`; apps choose handlers/levels
- **Secrets via `${ENV_VAR}`** — never in topology files, never in logs/error messages

## Git Workflow
1. Branch: `feat/<short-desc>` or `fix/<short-desc>` (off `main`)
2. Conventional commit prefixes: `feat:`, `fix:`, `chore:`, `docs:`, `test:`
3. Open a PR; link the issue with "Closes #<num>"
4. CI must be green (test matrix on Linux + Windows × Python 3.10–3.13, ruff, build,
   packaging) — no merge without green
5. Squash-merge to `main`
6. End commit messages with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Changelog & Release
- **DO update `CHANGELOG.md`** under `## [Unreleased]` in your PR (Keep a Changelog
  format). This project edits the changelog by hand — there is no release-please.
- Release: cut a GitHub **Release**; `release.yml` then builds and publishes to PyPI
  via Trusted Publishing (configure the publisher once at pypi.org). Don't upload
  to PyPI manually.

## Packaging (`packaging` CI job, #123)
CI's `test`/`examples` jobs install `.[dev]` from a repo checkout — they never
install what `pip install pyshal` actually produces, so a bad `Requires-Dist`
was never once exercised. That's exactly how `mcp>=1.0` shipped with no upper
bound and broke every fresh `pip install "pyshal[mcp]"` for 34 days while CI
stayed green (#105/#106). The `packaging` job builds the wheel and runs
`dev/packaging/check_dist.py` against its own METADATA (not `pyproject.toml`):
it fails if a third-party import that runs at **module scope** in `src/shal`
(i.e. at `import shal` time) isn't a declared base dependency, and if any
declared dependency has no upper bound and no written exemption
(`UNBOUNDED_EXEMPT` in that script). A **lazy** import — nested inside a
function/method, like the `mcp` SDK import in `src/shal/mcp/server.py` or the
`jsonschema` import in `src/shal/loader.py:_validate_schema` — is exempt from
the first check on purpose: it only breaks the feature that calls it, not
`import shal`. The clean-venv, no-extras wheel install (`shal --help` /
`shal docs` / `shal-mcp --help`, AGENT_GUIDE.md ships) already lives in the
`build` job; `packaging` doesn't repeat it — it's a static metadata check.

## Common Pitfalls
- Don't bypass any non-negotiable above to make a test pass — fix the root cause
- Run `ruff check src tests` before pushing (CI fails otherwise)
- `examples/` and `docs/` are not part of the distribution — don't add runtime
  deps for them or import them from `src/shal`
- A failure that needs a *design decision* rather than a bug fix → open an issue,
  don't guess
- Don't add a YAML node key without adding it to both the JSON Schema
  (`src/shal/schema/`) and the loader's `_NODE_KEYS`
- A change that affects authoring (node keys, transport kinds, registration, address
  grammar, the `@op`/tool/`catalog()` surface) without a matching `integrations/claude-code/skills/`
  update is **incomplete** — see "Keep the skills in sync"

## Agent skills

### Issue tracker

Issues and PRDs live in this repo's **GitHub Issues** (via the `gh` CLI). See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage vocabulary, used as-is (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

**Single-context** repo; locked decisions live in `docs/ARCHITECTURE.md` §5 (the Decision Ledger of record), design detail in `docs/design/DESIGN V2.md`, glossary in module docstrings. See `docs/agents/domain.md`.

## Asking Questions
Open an issue at https://github.com/determlab/shal/issues and tag @hemipaska.
