---
name: push-safe
description: Push a Srtforge branch only when the user explicitly asks, without exposing or storing credentials.
---

# push-safe

## When to use it

Use only when the user explicitly asks you to push.

## Files to read

- `CODEX.md`
- `AGENTS.md`
- `docs/agent/HANDOFF.md`
- `git status --short` output
- staged diff, if committing/pushing staged work

## Exact Windows PowerShell commands

```powershell
git branch --show-current
git status --short
git remote -v
git diff --cached --check
git config --local --get-regexp "remote|branch" | Select-String "github_pat|ghp_|token" -SimpleMatch
git push -u origin <branch>
git remote -v
git config --local --get-regexp "remote|branch" | Select-String "github_pat|ghp_|token" -SimpleMatch
```

## Acceptance output

- Current branch and status are confirmed.
- Main is not pushed.
- Staged diff has no obvious secrets.
- Normal `origin` remote is used.
- No inline PAT URL is used.
- Remote/config token scan is run before and after push.

## What not to do

- Do not push unless explicitly asked.
- Do not push `main`.
- Do not use inline PAT URLs.
- Do not alter remote URLs to include tokens.
- Do not paste or store credentials in `.git/config`, docs, shell history,
  screenshots, or handoff files.

## How to update `docs/agent/HANDOFF.md`

Record the pushed branch, push command result, and post-push remote/config scan
without printing secrets.

## Failure behavior

If authentication fails, stop and tell the user to authenticate Git Credential
Manager or GitHub CLI outside Codex. If suspicious token output appears,
report that suspicious output existed without printing the token.
