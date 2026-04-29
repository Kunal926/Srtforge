---
name: limit-safe-stop
description: Safely stop Codex work when context is long, tools are failing, or interruption is likely.
---

# limit-safe-stop

## When to use it

Use when context is running long, local tools are failing, work may be
interrupted, or the next edit would be risky without a durable checkpoint.

## Files to read

- Active ExecPlan or task note
- `docs/agent/HANDOFF.md`
- `docs/agent/WORKFLOW.md`
- `git status --short` output

## Exact Windows PowerShell commands

Run the narrowest meaningful available check, for example:

```powershell
python scripts/check_docs.py
python -m pytest tests/test_worker_protocol.py -q
git status --short
git diff --stat
```

## Acceptance output

Before stopping:

- risky edits are stopped,
- narrowest meaningful check is run or explicitly skipped with reason,
- active ExecPlan progress is updated,
- `docs/agent/HANDOFF.md` is updated,
- uncommitted files are recorded,
- commands run and results are recorded,
- exact next command/action is recorded.

## What not to do

- Do not say "I'll continue later" as the continuation state.
- Do not leave the next action vague.
- Do not keep editing after deciding to safe-stop.
- Do not hide failed or skipped checks.

## How to update `docs/agent/HANDOFF.md`

Add a continuation-ready snapshot: branch, dirty files, commands, failures,
skips, decisions, blockers, and exact next recommended action.

## Failure behavior

If even handoff editing fails, report the failed command and the best available
status in chat, then stop.
