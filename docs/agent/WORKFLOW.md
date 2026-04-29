# WORKFLOW.md

The default loop an agent should run inside Srtforge. It is shared by Claude
Code and OpenAI Codex; agent-specific manuals and skills adapt the same loop
to each tool.

Durable repo files are the source of truth. Chat-only decisions must be turned
into task notes, ExecPlans, docs, contracts, tests, ADRs, or `QUALITY.md`
before another agent is expected to rely on them.

## The Ralph loop

```
1. Read compact context.
2. Check git status.
3. Plan.
4. Patch one phase.
5. Run the narrowest useful check.
6. Fix.
7. Run broader lightweight checks.
8. Update handoff.
9. Summarize.
```

Concretely, on a fresh session:

### 1. Read compact context

- `AGENTS.md`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/agent/HANDOFF.md`
- `CLAUDE.md` for Claude Code or `CODEX.md` for Codex
- The active ExecPlan, if any, in `docs/agent/exec-plans/active/`.

That should be under a few hundred lines total. If you need more, follow
the laterally linked docs.

### 2. Check git status

```powershell
git status --short
git log --oneline -10
git branch --show-current
```

If there are uncommitted changes from a prior session, do not destroy them.
Document them in your plan and decide whether to fold them in or leave them
alone.

If switching agents, preserve both layers: Claude Code skills stay under
`.claude/skills/`; Codex skills stay under `.agents/skills/`; Codex local
actions stay under `.codex/`.

### 3. Plan

For small tasks, write a 5-line plan in chat or a `docs/agent/tasks/<slug>.md`
from `TASK_TEMPLATE.md`. For multi-hour / cross-cutting work, write or update
an ExecPlan in `docs/agent/exec-plans/active/<slug>.md` per `PLANS.md`.

### 4. Patch one phase

Make one coherent change. Don't bundle five unrelated things together.

### 5. Run the narrowest useful check

The check should be the most specific thing that can refute your change.

```powershell
python -m pytest tests/test_cli_worker.py::test_name
ruff check srtforge/cli.py
cd srtforge-studio && pnpm tsc --noEmit
```

### 6. Fix

If the narrow check fails, **diagnose root cause first**. Don't loop on
"retry until green" — read the error, understand it, then patch.

If the failure points at a missing harness capability (no test for this
case, missing fixture, unclear error, stale doc), add that capability
before retrying.

### 7. Run broader lightweight checks

Once the narrow check passes:

```powershell
pwsh ./scripts/check.ps1
```

This runs the full lightweight harness. Skipped checks should print clear
reasons.

### 8. Update handoff

Update `docs/agent/HANDOFF.md` with:
- What you changed.
- Commands you ran and their result.
- Skipped checks and why.
- Decisions made.
- Known blockers.
- Next recommended action.

If you changed the file layout, regenerate the project map:

```powershell
python scripts/update_context.py
```

If your change touched the worker protocol, update
`docs/contracts/worker-protocol.md` and the schemas alongside the code.

### 9. Summarize

Final response to the human follows the format in the active agent's
`pr-summary` skill:

- Claude Code: `.claude/skills/pr-summary/SKILL.md`
- Codex: `.agents/skills/pr-summary/SKILL.md`

```
## Summary
## Changed files
## Commands run
## Harness improvements added
## Known limitations
## Next recommended agent task
```

## Failure-domain rule

When you get stuck, the missing thing is almost always a missing harness
capability. Identify it explicitly and choose one:

- **Add it now** — write the test/fixture/doc/script/contract,
- **Track it** — add a row to `docs/agent/QUALITY.md` and proceed.

Never silently abandon a failure. Never ask the human to write code.

## Context-reset rule

After large changes, the next session should be able to continue from repo
files alone. Before stopping, make sure:

- `docs/agent/HANDOFF.md` is up to date.
- `docs/agent/CONTEXT_BRIEF.md` reflects current architecture (only update
  if architecture moved).
- The active ExecPlan, if any, has its progress checkboxes / discoveries /
  decisions / outcomes updated.
- `docs/agent/PROJECT_MAP.md` is regenerated if file layout changed.

## Human-steering rule

Human feedback during review must become one of:

- a task update in `docs/agent/tasks/`,
- an ADR in `docs/adr/`,
- a test in `tests/`,
- a schema or contract update in `docs/contracts/`,
- a doc update in `docs/agent/` or `docs/architecture/`,
- a row in `docs/agent/QUALITY.md`.

No important decision stays only in chat.
