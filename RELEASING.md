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
2. **Run the cold-start e2e gate** (manual, real hardware — *not* in CI). For each
   device you own, run `python eval/cold-start/stage_run.py --device {sonos|deebot}`,
   have the evaluator agent complete the report, then run the mechanical verifier
   `<venv python> eval/cold-start/verify_run.py runs/<device>-<timestamp>` — it must
   exit 0 for the run to count. Record the verdict (and any new wall). See
   `eval/cold-start/README.md`. The "arbitrary unknown hardware" path is the 1.0
   promise and out of scope here, so a `PARTIAL`/`FAIL` does not block the launch on
   its own — it documents the known wall you are shipping with. (A verifier *reject*,
   by contrast, means the run was invalid — re-run it honestly before relying on it.)
3. **Bump the version** in `pyproject.toml`.
4. **Cut the changelog**: rename `## [Unreleased]` → `## [X.Y.Z] - YYYY-MM-DD`,
   add a fresh empty `## [Unreleased]` above it.
5. **Commit**: `chore(release): vX.Y.Z`.
6. **Tag**: `git tag -a vX.Y.Z -m "pyshal X.Y.Z"` && `git push origin vX.Y.Z`.
7. **GitHub Release**: `gh release create vX.Y.Z --title "vX.Y.Z" --notes-from-tag`
   (or paste the changelog section). Publishing the Release triggers
   `.github/workflows/release.yml`, which builds and uploads to PyPI via **Trusted
   Publishing**.
8. **Verify**: in a clean venv, `pip install pyshal==X.Y.Z` → `import shal`.
9. **Close the milestone.**

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
