# AGENTS.md

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
Python 3.10+ required.

## Commands
| | |
|---|---|
| Test all | `pytest` |
| Test one | `pytest tests/test_shal.py::test_load_and_read` |
| Lint | `ruff check src tests` |
| Auto-fix lint | `ruff check src tests --fix` |
| Build | `python -m build` (sdist + wheel) |
| Sim demo | `python playground/mesh/demo_mesh.py` (microservice mesh) |
| | `python playground/deebot/demo_sim.py` (simulated robot vacuum) |

There is **no CLI entry point yet** (`shal` on the command line is not wired) and
**no mypy gate** — the code is fully type-hinted and the design is mypy-clean by
intent, but mypy is not in CI. Don't reference either as if it exists.

## Architecture
- `src/shal/loader.py` — YAML topology loader (safe_load, schema validation, env
  resolution, `use:` includes, `$ref` links)
- `src/shal/transport.py` — `Transport` base + typed kind mixins (`ByteTransport`,
  `CommandTransport`, `MessageTransport`, `Stream`)
- `src/shal/registry.py` — driver registry keyed by `compatible`, collision policy
- `src/shal/driver.py` — `Driver` base, `@idempotent`, `@op`, bind-time wrapping
- `src/shal/hal.py` — lookup API, lifecycle, LLM tool surface (`tool_schemas`/`call_tool`)
- `src/shal/node.py` `errors.py` `log.py` `logging.py` `capabilities.py`
- `src/shal/buses/` — `sim`, `local`, `ssh`, `i2c_cli`, `spi_cli`, `tcp`, `http_bus`, `mux`
- `src/shal/drivers/` — `tmp102` (the canonical driver)
- `src/shal/schema/shal-v1.schema.json` — the canonical topology schema
- `tests/` — pytest suite (mirrors `src/` concerns)
- `playground/` — runnable examples (Deebot cloud, microservice mesh); **not shipped**
- `docs/` — `DESIGN V2.md` (architecture, locked decisions), `DECISIONS - V2.1.md`

Read `docs/DESIGN V2.md` and `docs/DECISIONS - V2.1.md` before any core change.
The design decisions there are **locked** — don't re-litigate them in a PR.

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
`shal.drivers` entry point (bundled drivers are wired the same way in
`pyproject.toml`). Step-by-step guides live in `.claude/skills/`:
`shal-build-yaml`, `shal-build-bus`, `shal-build-driver`.

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
4. CI must be green (test matrix on Linux + Windows × Python 3.10–3.13, ruff, build)
   — no merge without green
5. Squash-merge to `main`
6. End commit messages with: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`

## Changelog & Release
- **DO update `CHANGELOG.md`** under `## [Unreleased]` in your PR (Keep a Changelog
  format). This project edits the changelog by hand — there is no release-please.
- Release: cut a GitHub **Release**; `release.yml` then builds and publishes to PyPI
  via Trusted Publishing (configure the publisher once at pypi.org). Don't upload
  to PyPI manually.

## Common Pitfalls
- Don't bypass any non-negotiable above to make a test pass — fix the root cause
- Run `ruff check src tests` before pushing (CI fails otherwise)
- `playground/` and `docs/` are not part of the distribution — don't add runtime
  deps for them or import them from `src/shal`
- A failure that needs a *design decision* rather than a bug fix → open an issue,
  don't guess
- Don't add a YAML node key without adding it to both the JSON Schema
  (`src/shal/schema/`) and the loader's `_NODE_KEYS`

## Asking Questions
Open an issue at https://github.com/hemipaska-maker/shal/issues and tag @hemipaska.
