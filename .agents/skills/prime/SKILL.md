---
name: prime
description: Read compact Srtforge context and summarize current Codex session state before making edits.
---

# prime

## When to use it

Use at the start of a fresh Codex session or after returning from a long
pause. This is read-only.

## Files to read

- `AGENTS.md`
- `CODEX.md`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/WORKFLOW.md`
- Any non-README file under `docs/agent/exec-plans/active/`

## Exact Windows PowerShell commands

```powershell
git status --short
git branch --show-current
git log --oneline -10
```

## Acceptance output

Output a short state summary with:

- project purpose in one sentence,
- current branch,
- clean/dirty git state,
- last 10 commit headline,
- previous session's next recommended action,
- active ExecPlans, if any.

## What not to do

- Do not modify files.
- Do not run tests or install dependencies.
- Do not inspect secrets, media, model files, `.venv`, `node_modules`, or
  generated bundles.

## How to update `docs/agent/HANDOFF.md`

Do not update handoff for a read-only prime unless the user explicitly asks
for a handoff refresh.

## Failure behavior

If a file is missing, report the missing path and continue with available
context. If Git commands fail, stop and report the exact command and error.
