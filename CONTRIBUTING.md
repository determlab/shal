# Contributing to SHAL

Thanks for your interest. SHAL is a standard-in-the-making, so the bar for the
core is deliberately high; extending it via drivers and buses is meant to be easy.

## Where complexity goes

> Complexity flows toward the rarest audience; simplicity flows toward users.

Three audiences, in order of how rare they are: **bus authors** (core/experts) →
**driver authors** (the community) → **end users** (write YAML, call
capabilities). A change that pushes complexity toward end users is rejected.
The locked design lives in [docs/design/DESIGN V2.md](docs/design/DESIGN%20V2.md) and
[docs/design/DECISIONS - V2.1.md](docs/design/DECISIONS%20-%20V2.1.md) — read them before
proposing core changes; the invariants there are not up for re-litigation in a PR.

## Project layout

```
src/shal/        the package (importable as `shal`)
tests/           pytest suite
docs/            design + decision records
examples/demos/  runnable showcases (Deebot cloud, microservice mesh) — not shipped
examples/driver-creator/  the doc→driver generation benchmark — not shipped
```

## Dev setup

```sh
pip install -e ".[dev]"
python -m pytest          # tests
ruff check src tests      # lint
python -m build           # sdist + wheel
```

Requires Python ≥ 3.10. CI runs the suite on Linux and Windows across 3.10–3.13.

## Adding a driver or bus

Don't edit the core to add hardware support — publish a package that exposes your
driver via the `shal.drivers` entry point (the bundled drivers are wired the same
way in `pyproject.toml`). The `.claude/skills/` folder has step-by-step guides:
`shal-build-driver`, `shal-build-bus`, `shal-build-yaml`.

## Pull requests

- Keep changes minimal and consistent with the documented invariants; the code
  comments state them explicitly.
- Every change ships with tests; the suite must stay green and `ruff` clean.
- A failure that needs a *design decision* rather than a bug fix → open an issue
  first, don't guess.
- Update `CHANGELOG.md` under `## [Unreleased]`.

## The non-negotiables (security & safety)

`yaml.safe_load` only · `CommandTransport` carries argv vectors, never shell
strings · a delivery-unknown write is never auto-retried · secrets via `${ENV}`,
never in topology files, never logged · the library never configures logging.
