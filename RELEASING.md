---
type: contributing
owner: repo-agent
scope: repo/shal
reviewed: 2026-09-29
---

# Releasing

How `pyshal` is versioned and published. (Distribution name: **`pyshal`**; import
name: **`shal`**.)

There are two release paths, and they have different owners:

- **Type A** — a version-bump + changelog PR. This is an ordinary PR: anyone
  (including an agent under the normal loop, subject to the usual review) can
  open one. CI builds and checks it like any other PR — the existing
  `test`/`packaging`/`build` jobs in `.github/workflows/ci.yml` cover it; there is
  no separate release-specific CI job for this step.
- **Type B** — cutting the actual release: tagging and `gh release create` (or
  the GitHub Release UI). Publishing a GitHub Release is the one action that
  triggers `.github/workflows/release.yml`, which publishes to PyPI via Trusted
  Publishing. **Only the founder does this step.** No agent holds PyPI
  credentials, and no agent performs this step — it's the one irreversible,
  external action (a real release, published to PyPI), so it stays human-only.

## Versioning (SemVer, pre-1.0)

While in `0.x` (alpha):

| Bump | When | Example |
|------|------|---------|
| **minor** `0.X.0` | new feature, driver, bus, or capability | `0.1.0 → 0.2.0` |
| **patch** `0.1.X` | fixes, docs, packaging, tests — no new public API | `0.1.0 → 0.1.1` |

Breaking changes are allowed pre-1.0 but **must** be called out in the changelog
under a `### Changed` / `### Removed` heading. The single source of truth for the
version is `version` in `pyproject.toml`.

## Issues ↔ versions

Work is planned with **GitHub Milestones, one per version**. Every issue gets a
milestone; the milestone page is the release scope + progress bar. Open an issue
against the version you intend it for (e.g. `v0.2.0`).

## Type A — the version-bump + changelog PR

Anyone can open this PR once the milestone's work is ready:

1. **Land the work.** All issues for the milestone merged to `main`, CI green.
2. **Bump the version** in `pyproject.toml`.
3. **Cut the changelog**: rename `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it.
4. **Commit and open a PR**: `chore(release): vX.Y.Z`. CI runs the normal
   `test`/`packaging`/`build` checks on it, and it goes through the usual review
   — nothing about the version bump itself is gated or hard-stopped (a pure
   version bump is not a new dependency).

That PR merging to `main` does **not** publish anything. Publishing only happens
via Type B, below.

## Type B — cutting the release (founder only)

Once the Type A PR is merged to `main`:

1. **Tag**: `git tag -a vX.Y.Z -m "pyshal X.Y.Z"` && `git push origin vX.Y.Z`.
2. **GitHub Release**: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag`
   (or paste the changelog section) — or the equivalent through the GitHub
   Release UI. Publishing the Release triggers `.github/workflows/release.yml`,
   which builds and uploads to PyPI via **Trusted Publishing** (OIDC — no
   token/secret an agent could hold).
3. **Verify**: in a clean venv, `pip install pyshal==X.Y.Z` → `import shal`.
4. **Close the milestone.**

This whole step is performed by the founder, not an agent: it's the one action
in the release process that is irreversible and external (a real package
published to PyPI under the project's name).

## One-time setup (required before automated publish works)

Configure **Trusted Publishing** for the project on PyPI — once, by a maintainer:
<https://pypi.org/manage/account/publishing/>

- PyPI project: `pyshal`
- Owner: `determlab` · Repo: `shal`
- Workflow: `release.yml` · Environment: `pypi`

Until this is configured, `release.yml` cannot publish (it uses OIDC, not a token).
`pyshal 0.1.0` was published manually with a token before TP was set up; from
`0.1.1` onward, use the Type B flow above.

> Manual fallback (if you must): `python -m build` then
> `twine upload dist/*` with a PyPI API token in `TWINE_USERNAME=__token__` /
> `TWINE_PASSWORD`. Prefer the automated Type B path. This fallback is also
> founder-only, for the same reason: it needs real PyPI credentials.
