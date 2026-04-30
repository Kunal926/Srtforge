# TASK_TEMPLATE.md

Copy this to `docs/agent/tasks/<slug>.md` for small (~1 hour) tasks.
For larger or cross-cutting work, use an ExecPlan instead — see `PLANS.md`.

```markdown
# Task: <imperative slug>

## Objective

One paragraph. What user-visible outcome should this produce?

## Non-goals

What this task explicitly does **not** do. Helps a future reader (and you)
resist scope creep.

## User-visible outcome

What the human owner sees after this lands — a new event in the UI, a new
command, a new test passing, a doc update.

## Files likely involved

- `path/to/file.py` — why this file
- `path/to/other.ts` — why this file

## Acceptance criteria

Bulleted, observable, testable. Examples:

- `python -m pytest tests/test_x.py::test_y` passes.
- `.\.codex\actions\harness-check.ps1` is green.
- `srtforge worker` emits `<event>` with shape `…` when given input `…`.

## Commands to run

Exact commands the agent will use to validate. Both narrow and broad:

```powershell
python -m pytest tests/test_<x>.py -k <pattern>
.\.codex\actions\harness-check.ps1
```

## Risks

- What could break? (Worker protocol consumers? Existing tests? UI users?)
- What's the blast radius if this lands wrong?

## Rollback notes

How to revert if the task ships and breaks something. Usually
`git revert <sha>`; note any DB / settings file migrations that need
manual cleanup.

## Handoff update requirement

Confirm `docs/agent/HANDOFF.md` was updated with:
- Commands run and result.
- Skipped checks and why.
- Next recommended action.
```
