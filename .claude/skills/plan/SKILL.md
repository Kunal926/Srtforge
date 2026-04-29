---
name: plan
description: Create a small-task plan with explicit acceptance criteria and the narrow checks that prove it's done. Use this for ~1-hour scoped work that does not need a full ExecPlan.
---

# plan

Produces a concise plan file at `docs/agent/tasks/<slug>.md` from
`docs/agent/TASK_TEMPLATE.md`. Use this for single-layer, short work.
For anything multi-hour or cross-cutting, use the `execplan` skill
instead.

## Steps

1. Pick a slug (verb-first kebab-case: `fix-zombie-worker-shutdown`,
   `wire-progress-events`).
2. Read `docs/agent/TASK_TEMPLATE.md` to remember the required sections.
3. Write the new task file at `docs/agent/tasks/<slug>.md`. Fill in:
   - Objective (one paragraph),
   - Non-goals,
   - User-visible outcome,
   - Files likely involved (with brief "why"),
   - Acceptance criteria (observable, testable),
   - Commands to run (narrow + broad),
   - Risks,
   - Rollback notes,
   - Handoff update requirement.
4. Confirm the task does not duplicate an existing entry in
   `docs/agent/QUALITY.md` — if it does, link to it from the task and
   strike the QUALITY entry once the task lands.

## Constraints

- Each acceptance criterion must be **observable**: a test name, a
  command's exit status, an event shape, or a manual visual check.
- "Broad" command: always `pwsh ./scripts/check.ps1` (or
  `bash ./scripts/check.sh` on Unix).
- Do not promise more than ~1 hour of work in a single task. If scope
  expands during execution, escalate to an ExecPlan.

## After the plan exists

Hand control back to the user. Do not start implementing in the same
session unless the user asks you to. The plan file is the contract.
