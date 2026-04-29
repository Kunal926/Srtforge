---
name: execplan
description: Create or update a self-contained ExecPlan for multi-hour or cross-cutting Srtforge work. Use this for protocol changes, packaging changes, or anything that touches Python plus Rust plus TypeScript together.
---

# execplan

Produces a self-contained ExecPlan at
`docs/agent/exec-plans/active/<slug>.md`. ExecPlans must survive a
context reset — a fresh agent should be able to execute the plan from
the file alone.

## When to use this skill

Use **all** of these as triggers:

- estimated work > 2 hours,
- touches Python + Rust + TypeScript together,
- changes the worker protocol, the settings schema, or the pipeline
  shape,
- changes CI or build/packaging,
- crosses architectural boundaries.

For single-hour single-layer work, use `plan` instead.

## Steps

1. Read `docs/agent/PLANS.md` (full convention).
2. Read `docs/agent/CONTEXT_BRIEF.md` and the latest `HANDOFF.md`.
3. If an ExecPlan already exists in
   `docs/agent/exec-plans/active/` for related work, **update** that
   one rather than creating a new one. Append a "Surprises and
   discoveries" entry instead of starting over.
4. Otherwise, create
   `docs/agent/exec-plans/active/<verb-slug>.md` with the required
   sections from `PLANS.md`:
   - Purpose / big picture
   - Context and orientation
   - Progress (checkbox list)
   - Milestones
   - Steps (concrete, numbered, runnable)
   - Validation and acceptance
   - Surprises and discoveries
   - Decision log
   - Outcomes and retrospective
   - Idempotence and recovery
5. Inline only the snippets the next agent needs to act on without
   further searching. Link to files for everything else.
6. Each step must include exact commands (paths, env vars, flags).
7. The final phase should always include "Update HANDOFF.md and move
   this ExecPlan to `archive/`."

## Constraints

- The ExecPlan must be executable on Windows (PowerShell-friendly
  commands). Note Unix-only steps explicitly.
- Do not include real secrets, real media paths, or model URLs.
- "Acceptance" must be testable, not "looks right".
- Idempotence: each step must be safe to rerun. If a step is
  destructive, document the safe-recovery path.

## After the plan exists

Update `docs/agent/HANDOFF.md` to reference the new plan. Then either
hand control back to the user, or — if the user asked to start now —
execute Phase 1 of the plan and re-prompt for confirmation.
