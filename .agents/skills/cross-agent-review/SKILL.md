---
name: cross-agent-review
description: Review Claude Code or another agent's Srtforge work for harness, protocol, safety, and handoff discipline.
---

# cross-agent-review

## When to use it

Use when asked to review another agent's branch, patch, handoff, or claimed
completion.

## Files to read

- `AGENTS.md`
- `CODEX.md`
- `CLAUDE.md`
- `docs/agent/HANDOFF.md`
- Active task note or ExecPlan
- Changed files from `git status --short` / `git diff --name-only`
- Protocol files if worker protocol changed

## Exact Windows PowerShell commands

```powershell
git status --short
git diff --stat
git diff --name-only
python scripts/check_docs.py
```

For protocol changes:

```powershell
python -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q
```

## Acceptance output

Answer these questions:

- Did it follow `AGENTS.md`?
- Did it update handoff?
- Did it use an ExecPlan when required?
- Did it run checks?
- Did it hide failures?
- Did protocol changes touch Python/Rust/TypeScript/docs/schema/tests?
- Did it introduce secrets?
- Did it require heavy resources in default tests?
- What should Codex fix next?

## What not to do

- Do not rewrite unrelated work during review.
- Do not trust chat claims over repo files.
- Do not print secrets if suspicious output appears; identify the file/path
  and redact the value.

## How to update `docs/agent/HANDOFF.md`

If review finds actionable gaps and you fix them, update handoff with findings,
commands run, and next action. For review-only work, handoff update is optional
unless the user asks.

## Failure behavior

If checks cannot run, record the environment blocker and continue the static
review. If a secret is suspected, stop and report the path without exposing it.
