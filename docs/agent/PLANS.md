# PLANS.md — ExecPlan convention

Srtforge uses **ExecPlans** for any work that is multi-hour, cross-cutting,
protocol-changing, CI-changing, or otherwise needs to survive a context
reset. ExecPlans live in:

```
docs/agent/exec-plans/active/<slug>.md     # while in progress
docs/agent/exec-plans/archive/<slug>.md    # after completion
```

A small bug fix in one file does not need an ExecPlan — write a task note
in `docs/agent/tasks/<slug>.md` from `TASK_TEMPLATE.md` instead.

## When to use an ExecPlan

Use one when **any** of these is true:

- Estimated work > 2 hours.
- Touches Python + Rust + TypeScript together.
- Touches the worker protocol, settings schema, or pipeline shape.
- Touches CI or build/packaging.
- Changes architectural boundaries.
- Will be paused and resumed across sessions.

## ExecPlan structure

A fresh agent must be able to execute the plan from the file alone.
Required sections, in order:

```markdown
# ExecPlan: <slug>

## Purpose / big picture

Why this work exists, what user-visible outcome it produces, and what the
"done" picture looks like.

## Context and orientation

Pointers to the files, prior ADRs, prior commits, related ExecPlans, and
tests this work touches. Inline only the snippets that the next agent
needs to act on without further searching.

## Progress

Checkbox list of phases / milestones. **Update this in place** — don't
delete completed items, mark them `[x]`.

- [ ] Phase 1: …
- [ ] Phase 2: …
- [ ] Phase 3: …

## Milestones

What "Phase N done" means observably (a passing test, a working command,
an emitted event shape, a frontend that compiles).

## Steps

Concrete numbered steps. Each step must be:
- self-contained,
- runnable on Windows + PowerShell,
- testable.

Include exact commands (paths, env vars, flags). No "and then run the
relevant tests" — name the test.

## Validation and acceptance

What proves the plan succeeded. Both:
- automated checks (pytest selection, ruff, ts compile, cargo check), and
- manual checks if any (e.g. "open the app, verify the stage dots animate").

## Surprises and discoveries

Non-obvious things you learned while executing. Update as you go. Helps
the next agent (and you, after a context reset) avoid repeating the
investigation.

## Decision log

For decisions that affect the plan or the repo. Each entry: date, what
was decided, why, alternatives considered, who decided. Link to ADRs for
decisions that outlive the plan.

## Outcomes and retrospective

Filled in at the end:
- What shipped.
- What was descoped (and why).
- What follow-ups landed in `QUALITY.md` or new ExecPlans.
- What the next agent should do with this knowledge.

## Idempotence and recovery

What happens if a step partially completes and the agent restarts?
Document the safe-resume point for each phase. Include the recovery
command (e.g. "rerun phase 3 from scratch by deleting `target/` first").
```

## Lifecycle

1. Create file in `active/`.
2. Mark progress checkboxes as you go. Add to "Surprises" liberally.
3. When the plan is **complete and validated**, move the file to
   `archive/` and update its "Outcomes and retrospective" section.
4. If the plan is **abandoned**, move it to `archive/` with an "Outcomes"
   section that explains why.

## ExecPlan vs task note

| Property | Task note (`docs/agent/tasks/`) | ExecPlan (`docs/agent/exec-plans/active/`) |
| --- | --- | --- |
| Scope | < 2 hours, single layer | Multi-hour or cross-layer |
| Sections | Objective, files, acceptance, commands | Full ExecPlan structure above |
| Progress tracking | Often a single TODO | Checkbox list |
| Decision log | Inline if needed | Required section |
| Survives context reset? | Maybe | Must |
