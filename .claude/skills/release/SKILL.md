---
name: release
description: Cut a reacher release at any channel (alpha, beta, rc, or stable) — bump version, recompile firmware hex, commit, tag, and push to trigger the PyPI + GitHub release pipeline. Use when the user asks to release, cut a release, or publish a new reacher version.
argument-hint: "[version, e.g. 3.4.0 or 3.4.0-beta.1]"
model: haiku
context: fork
agent: general-purpose
background: false
allowed-tools: Agent, AskUserQuestion, Bash(git *), Bash(gh *), Bash(python scripts/bump-version.py *), Bash(bash firmware/compile.sh)
---

# Cut a reacher release

## Dispatch

First, decide which of the two cases below applies to you.

- **You are already the isolated worker for this skill** — this content reached you as the `prompt` of an `Agent` call, or you were forked directly via this skill's own `context: fork`/`model: haiku` configuration (for example, you were invoked through the `Skill` tool rather than a typed `/release` command). In that case: skip the rest of this Dispatch section and go straight to "When this skill is active" below. Do the actual work yourself, in this same turn, including using `AskUserQuestion` directly for every confirmation gate described below — do not describe readiness and stop, do not end your turn waiting to be resumed, and do not spawn another `Agent` call for any of it.
- **You are the top-level assistant in the main conversation**, and this content just arrived as a plain instruction (e.g. a typed `/release` slash command injected it directly into your turn). In that case, follow steps 1–3 below: you must not do the git/gh/version work yourself, because this path does not fork you automatically.

If genuinely unsure which case applies, prefer treating yourself as already-isolated and doing the work directly — an extra layer of delegation is worse than none.

1. Do not run any `git`, `gh`, or `python scripts/bump-version.py` command yourself in this context, and do not answer any of the confirmation gates below yourself.
2. Immediately call the `Agent` tool, once, and wait for it to finish:
   - `subagent_type: "general-purpose"`
   - `model: "haiku"`
   - `run_in_background: false`
   - `description`: a short label like "Release: reacher version bump/tag/publish"
   - `prompt`: the full text of this file from "## When this skill is active" to the end of "## Gotchas" (i.e. everything below this Dispatch section), followed by the user's original request verbatim (including the target version if given) and any relevant conversation context.
   - Because the call is synchronous, the subagent handles every confirmation gate itself via `AskUserQuestion`, getting a live answer from the user before returning. You do not relay questions or resume it — you only get back a finished result.
3. Relay the subagent's final result to the user as-is.

Everything below this point is worker instructions delegated via the `prompt` in step 2 above — it assumes it is that subagent, operating with `Bash(git *)`, `Bash(gh *)`, `Bash(python scripts/bump-version.py *)`, `Bash(bash firmware/compile.sh)`, and `AskUserQuestion` only. Wherever these instructions say "stop and ask" or "pause for confirmation," that means call `AskUserQuestion` directly and wait for the answer — never end the turn without it.

## When this skill is active

Use this skill when the user clearly asks to cut, publish, or release a new reacher version, at any channel (alpha, beta, rc, or stable).

Never start a version bump, commit, tag, or push on your own initiative — only when explicitly requested.

## Steps

### 1. Prerequisites

```bash
git branch --show-current
git status --short
git pull origin main
```

- Refuse to proceed on a detached HEAD or any branch other than `main`; ask the user to switch first.
- If `git status --short` shows unexpected changes (anything you didn't just pull), stop and report — do not proceed on a dirty tree.

Check CI is green on the latest `main` commit:

```bash
gh run list --branch main --limit 1
```

If the most recent run isn't `completed`/`success`, stop and report — do not cut a release on top of a red or in-flight build.

### 2. Determine the target version

Take the version from the user's request (or `$ARGUMENTS`). Validate it matches `^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?$` (e.g. `3.4.0`, `3.4.0-alpha.1`, `3.4.0-beta.2`, `3.4.0-rc.1`).

If no version was given, or it doesn't match, ask via `AskUserQuestion` for an explicit target version. Do not guess or auto-increment.

Print the current version for context:

```bash
python scripts/bump-version.py
```

### 3. Bump and verify

```bash
python scripts/bump-version.py <version>
python scripts/bump-version.py --check <version>
```

If `--check` reports any `MISMATCH`, stop and report which file failed — do not proceed to commit a partially-stamped tree.

### 4. Recompile firmware hex

```bash
bash firmware/compile.sh
```

This is a hard requirement, not optional — the firmware sketches were just re-stamped with the new version string in step 3, and the committed hex artifacts in `src/reacher/hex/` must reflect that. If this script fails (missing `arduino-cli`, missing `arduino:avr` core, or a compile error), stop and report the failure. Do not tag a release with stale hex.

### 5. Review and confirm the commit

```bash
git status --short
git diff --stat
```

Show the user the changed files (should be: `pyproject.toml`, `src/reacher/__init__.py`, `firmware/libraries/REACHERDevices/library.properties`, the 6 sketch `.ino` files, `README.md`, and refreshed files under `src/reacher/hex/`). If anything else is staged/modified that you don't recognize as part of this flow, flag it before continuing.

Ask via `AskUserQuestion` to confirm before staging and committing.

### 6. Commit

```bash
git add -A
git commit -m "release: v<version>"
```

This repo's own commit history uses exactly this `release: vX.Y.Z` format for every past release — use it verbatim, not the `feat:`/`bug:`/`chore:`/`docs:` convention.

### 7. Push to main

Ask via `AskUserQuestion` to confirm before pushing (this updates the shared `main` branch).

```bash
git push origin main
```

### 8. Tag and push the tag

This is the highest-stakes step — pushing the tag triggers the real release pipeline (PyPI publish + GitHub Release). Ask via `AskUserQuestion` to confirm explicitly before doing this, showing the exact tag and message you're about to push.

```bash
git tag -a v<version> -m "REACHER v<version>"
git push origin v<version>
```

### 9. Watch the release pipeline

```bash
gh run list --branch main --limit 1
```

Offer to watch it live:

```bash
gh run watch <run-id>
```

Report the outcome of each job (`version-check`, `build-wheel`, `publish-pypi`, `release`).

If `publish-pypi` fails with `invalid-publisher`, the PyPI Trusted Publisher isn't registered (or its claims don't match) — report this to the user; the GitHub Release and wheel build still succeed independently, and once the publisher is fixed on PyPI, the failed job can be re-run with:

```bash
gh run rerun --failed
```

### 10. Final report

Summarize: version released, commit hash, tag, push status, and the CI job outcomes from step 9.

## Hard rules

- Never bump, commit, tag, or push without the user having explicitly requested a release in the current conversation.
- Never skip the firmware recompile step (4) — stale hex silently ships an outdated firmware version string.
- Never push the release tag (step 8) without an explicit confirmation for that specific tag.
- Never use `git push --force` or any force variant.
- Never hand-edit version-bearing files directly — always go through `scripts/bump-version.py`.
- Never proceed past a `--check` mismatch or a `firmware/compile.sh` failure.

## Gotchas

- The reacher project has no dependency on `labrynth` — this skill never reads or touches anything in the `labrynth` repo.
- Any semver channel is valid here (alpha/beta/rc/stable) — unlike `labrynth`'s `/release`, this skill is not restricted to stable.
- `--check` validates derived spellings too (shields.io badge escaping, PEP 440 wheel filename) — a mismatch there is still a real mismatch, not a false positive.
- A successful tag push is a valid completion of this skill even if you don't wait for the full CI run to finish — offer to watch it, but don't block indefinitely if the user wants to move on.
