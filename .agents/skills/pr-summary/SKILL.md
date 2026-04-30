---
name: pr-summary
description: Produce the exact final or pull-request summary format expected for Srtforge agent work.
---

# pr-summary

## When to use it

Use at the end of a task after checks and handoff are updated.

## Files to read

- `docs/agent/HANDOFF.md`
- Active task note or ExecPlan
- `git status --short`
- `git diff --stat`

## Exact Windows PowerShell commands

```powershell
git status --short
git diff --stat
```

## Acceptance output

Output exactly these headings:

```markdown
## Summary

## Changed files

## Commands run

## Harness improvements added

## Known limitations

## Next recommended agent task
```

## What not to do

- Do not add extra top-level headings.
- Do not claim checks passed if they did not run.
- Do not include secrets, tokens, or inline credential URLs.

## How to update `docs/agent/HANDOFF.md`

Use handoff as the source of truth. If the summary reveals missing handoff
details, update `docs/agent/HANDOFF.md` before producing the final summary.

## Failure behavior

If status or diff cannot be read, say so under "Known limitations" and include
the exact failed command.
