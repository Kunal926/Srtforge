---
name: handoff
description: Update docs/agent/HANDOFF.md so another agent can resume Srtforge work from repo files alone.
---

# handoff

## When to use it

Use before ending every Codex session, before a risky context reset, and after
any task that changes files.

## Files to read

- `docs/agent/HANDOFF.md`
- `docs/agent/CONTEXT_BRIEF.md`
- Active ExecPlan or task note
- `git status --short` output

## Exact Windows PowerShell commands

```powershell
git branch --show-current
git status --short
git diff --stat
```

If the file layout changed:

```powershell
python scripts/update_context.py
python scripts/check_docs.py
```

## Acceptance output

`docs/agent/HANDOFF.md` includes:

- current branch,
- git state,
- changed files,
- commands run,
- skipped checks,
- decisions,
- blockers,
- next recommended action.

## What not to do

- Do not leave "next steps" vague.
- Do not paste secrets, real media paths, or token-bearing remotes.
- Do not summarize failures as success.

## How to update `docs/agent/HANDOFF.md`

Edit the file in place, preserving the required section headings validated by
`scripts/check_docs.py`.

## Failure behavior

If handoff cannot be updated, stop further edits and report the blocker. A
session is not complete until durable continuation state exists in the repo.
