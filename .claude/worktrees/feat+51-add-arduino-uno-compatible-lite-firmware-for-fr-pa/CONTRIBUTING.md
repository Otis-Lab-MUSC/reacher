# Contributing to `reacher`

Thanks for working on the REACHER backend + firmware. This guide covers the
branch, versioning, and commit conventions. Release mechanics live in
[`RELEASING.md`](RELEASING.md).

## Branching model

`main` is the only permanent branch. It is always releasable and is the GitHub
default branch.

- **Never commit directly to `main` for non-trivial work.** Branch off `main`:
  - `feat/<slug>` — new functionality
  - `fix/<slug>` — bug fixes
  - `chore/<slug>` — tooling, deps, housekeeping
  - `docs/<slug>` — documentation only
- Open a pull request into `main`. Delete the branch after merge.
- There is **no `develop` branch.** (It was retired in June 2026 when the v3
  line moved to a `main`-based flow.) Long-lived feature branches are
  discouraged — rebase on `main` often and merge early.

## Versioning

This repo follows [semantic versioning](https://semver.org/) with an explicit
prerelease ladder:

```
X.Y.Z-alpha.N  →  X.Y.Z-beta.N  →  X.Y.Z-rc.N  →  X.Y.Z
```

- **Never hand-edit version strings.** Run the single source of truth:
  ```bash
  python scripts/bump-version.py X.Y.Z-alpha.1   # set
  python scripts/bump-version.py --check X.Y.Z-alpha.1   # verify (CI uses this)
  ```
  It stamps `pyproject.toml`, `src/reacher/__init__.py`, the firmware
  `library.properties`, and every sketch's `SendIdentification()` string in one
  pass — the firmware version is coupled to the package version.
- **Recompile firmware hex after any bump** so shipped binaries report the new
  version: `bash firmware/compile.sh`, then commit the refreshed
  `src/reacher/hex/`.

## Firmware changes

`firmware/libraries/REACHERDevices/src/Commands.h` (firmware) and
`src/reacher/kernel/commands.py` (backend) must stay in sync —
`tests/test_command_parity.py` enforces it. Edit both together when adding a
command. See `firmware/CLAUDE.md` for paradigm/timing detail.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/) — the tooling
and release notes assume it:

```
feat(kernel): add per-device lever routing filter
fix(uploader): resolve hex path under PyInstaller frozen mode
chore(deps): bump fastapi to 0.115
```

## Before opening a PR

```bash
ruff check . && ruff format --check .
pytest
```
