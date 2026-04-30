---
name: plan
description: Create a compact task note for small single-layer Srtforge work that does not need an ExecPlan.
---

# plan

## When to use it

Use for small tasks that are expected to stay under two hours, touch one
layer, and do not change worker protocol, CI, packaging, or architecture.

## Files to read

- `AGENTS.md`
- `CODEX.md`
- `docs/agent/WORKFLOW.md`
- `docs/agent/TASK_TEMPLATE.md`
- `docs/agent/HANDOFF.md`
- Relevant source or doc files for the task

## Exact Windows PowerShell commands

```powershell
git status --short
git branch --show-current
Copy-Item docs/agent/TASK_TEMPLATE.md docs/agent/tasks/<slug>.md
```

After editing the task note, run the narrow command named in the plan, for
example:

```powershell
python -m pytest tests/test_worker_protocol.py -q
```

## Acceptance output

- `docs/agent/tasks/<slug>.md` exists.
- It records objective, non-goals, files, acceptance criteria, commands,
  risks, rollback notes, and handoff requirement.
- It states why an ExecPlan is not required.

## What not to do

- Do not use this for worker protocol, Python+Rust+TypeScript, CI,
  architecture, or resumable work; use `execplan`.
- Do not leave acceptance criteria vague.
- Do not create duplicate task notes for the same work.

## How to update `docs/agent/HANDOFF.md`

Reference the task note under "Current goal" or "Next recommended action" and
record commands run once implementation starts.

## Failure behavior

If the task grows beyond the small-task boundary, stop and convert it into an
ExecPlan before continuing.
