# Releasing `reacher`

Releases are cut from `main` and driven by **git tags**. Pushing a
`v*.*.*` tag triggers `.github/workflows/main.yml`, which runs:

```
version-check  →  build-wheel  →  publish-pypi  →  release (GitHub Release)
```

`version-check` fails the build if the tag does not match the version stamped in
the source files, so the tag is the source of truth and cannot drift.

## Prerelease ladder

| Channel | Tag example | PyPI | GitHub Release |
|---|---|---|---|
| Alpha | `v3.1.0-alpha.1` | prerelease | Pre-release |
| Beta | `v3.1.0-beta.1` | prerelease | Pre-release |
| Release candidate | `v3.1.0-rc.1` | prerelease | Pre-release |
| Stable | `v3.1.0` | final | Latest |

The workflow marks a GitHub Release as a prerelease when the tag contains
`-alpha`, `-beta`, or `-rc`. PyPI treats PEP 440 prerelease versions
(`3.1.0a1`, `3.1.0b1`, `3.1.0rc1`) as prereleases automatically — `pip install
reacher2p` skips them unless `--pre` is passed or the dependency pin requests one
(e.g. `reacher2p>=3.0.0a1`).

## Cutting a release

Prereleases may be cut freely from a feature branch or `main`. **Stable
releases are cut only from `main` after CI is green.**

```bash
# 1. Land all changes on main via PR (CI green).
git checkout main && git pull

# 2. Bump + verify (never hand-edit versions).
python scripts/bump-version.py 3.1.0          # or 3.1.0-alpha.1, etc.
python scripts/bump-version.py --check 3.1.0

# 3. Recompile firmware hex so binaries report the new version.
bash firmware/compile.sh

# 4. Commit, push, tag.
git add -A && git commit -m "release: v3.1.0"
git push origin main
git tag -a v3.1.0 -m "REACHER v3.1.0"
git push origin v3.1.0          # ← triggers the release pipeline
```

## PyPI trusted publishing (one-time setup)

The `publish-pypi` job uses [PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/)
(OIDC, no stored token). It requires a **Trusted Publisher** registered on PyPI
for the `reacher` project, pointing at:

- Owner: `Otis-Lab-MUSC`
- Repository: `reacher`
- Workflow: `main.yml`

If `publish-pypi` fails with `invalid-publisher`, the Trusted Publisher is not
configured (or its claims don't match) — add it under the project's
**Publishing** settings on PyPI. The GitHub Release and built wheel still
succeed independently of PyPI, so you can re-run just the failed job with
`gh run rerun --failed` once the publisher is registered.

## Downstream

`labrynth` pins `reacher2p>=X.Y.Z` in its `pyproject.toml` (PyPI distribution
name; the import package is still `reacher`). After a `reacher` release, bump
that pin in labrynth to ship the new backend + firmware.
