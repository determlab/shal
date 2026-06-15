# Releasing

How `pyshal` is versioned and published. (Distribution name: **`pyshal`**; import
name: **`shal`**.)

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

## Cutting a release

1. **Land the work.** All issues for the milestone merged to `main`, CI green.
2. **Bump the version** in `pyproject.toml`.
3. **Cut the changelog**: rename `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it.
4. **Commit**: `chore(release): vX.Y.Z`.
5. **Tag**: `git tag -a vX.Y.Z -m "pyshal X.Y.Z"` && `git push origin vX.Y.Z`.
6. **GitHub Release**: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag`
   (or paste the changelog section). Publishing the Release triggers
   `.github/workflows/release.yml`, which builds and uploads to PyPI via **Trusted
   Publishing**.
7. **Verify**: in a clean venv, `pip install pyshal==X.Y.Z` → `import shal`.
8. **Close the milestone.**

## One-time setup (required before automated publish works)

Configure **Trusted Publishing** for the project on PyPI — once, by a maintainer:
<https://pypi.org/manage/account/publishing/>

- PyPI project: `pyshal`
- Owner: `determlab` · Repo: `shal`
- Workflow: `release.yml` · Environment: `pypi`

Until this is configured, `release.yml` cannot publish (it uses OIDC, not a token).
`pyshal 0.1.0` was published manually with a token before TP was set up; from
`0.1.1` onward, use the Release flow above.

> Manual fallback (if you must): `python -m build` then
> `twine upload dist/*` with a PyPI API token in `TWINE_USERNAME=__token__` /
> `TWINE_PASSWORD`. Prefer the automated Release path.
