---
name: execplan
description: Create or update a durable ExecPlan for cross-cutting, protocol, CI, architecture, or long-running Srtforge work.
---

# execplan

## When to use it

Use when work changes worker protocol, touches Python + Rust + TypeScript,
changes CI, changes architecture, may exceed context/session limits, needs
resumption by another agent, or is likely to take more than two hours.

## Files to read

- `AGENTS.md`
- `CODEX.md`
- `docs/agent/PLANS.md`
- `docs/agent/WORKFLOW.md`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/agent/HANDOFF.md`
- Existing related plans in `docs/agent/exec-plans/active/`
- Relevant contracts, ADRs, and source files

## Exact Windows PowerShell commands

```powershell
git status --short
git branch --show-current
New-Item -ItemType Directory -Force docs/agent/exec-plans/active
New-Item -ItemType File docs/agent/exec-plans/active/<slug>.md
python scripts/check_docs.py
```

## Acceptance output

`docs/agent/exec-plans/active/<slug>.md` has every required section from
`docs/agent/PLANS.md`:

- Purpose / big picture
- Context and orientation
- Progress
- Milestones
- Steps
- Validation and acceptance
- Surprises and discoveries
- Decision log
- Outcomes and retrospective
- Idempotence and recovery

## What not to do

- Do not write a plan that depends on chat-only context.
- Do not omit exact commands.
- Do not include secrets, real media paths, model URLs, or token-bearing
  remotes.
- Do not keep multiple active plans for the same work.

## How to update `docs/agent/HANDOFF.md`

Record the active ExecPlan path, current progress checkbox, blockers, checks
already run, and the exact next action.

## Failure behavior

If the plan cannot be made self-contained, stop implementation and add the
missing context to the plan before editing code.
