# docs/agent/

This directory is the home for everything an agent needs to operate inside
Srtforge: workflow, context briefs, plans, handoffs, quality tracking, and
tasks. The root entry point for all agents is `AGENTS.md`; this directory
holds the shared durable context it links to.

Claude Code uses `CLAUDE.md` and `.claude/skills/`. OpenAI Codex in the
Windows app, CLI, or IDE uses `CODEX.md` and `.agents/skills/`. Codex app
local actions live in `.codex/`. All agents share `docs/agent/*`,
`docs/contracts/*`, `docs/architecture/*`, and `docs/adr/*` as the source of
truth.

## Human-owner workflow

Srtforge is set up so most coding work can be agent-generated. The expected
loop between the human owner and the coding agent looks like this:

1. **Human gives objective.** A sentence or paragraph stating what should
   change and why.
2. **Agent creates or updates a task / ExecPlan.**
   - Small change → `docs/agent/tasks/<slug>.md` from
     `docs/agent/TASK_TEMPLATE.md`.
   - Multi-hour or cross-cutting change → ExecPlan in
     `docs/agent/exec-plans/active/<slug>.md` per `PLANS.md`.
3. **Agent implements** (Ralph loop in `WORKFLOW.md`).
4. **Agent runs the lightweight check** (`scripts/check.ps1`) and any
   targeted pytest selections.
5. **Agent updates `docs/agent/HANDOFF.md`** before stopping.
6. **Human reviews / steers / merges.**
7. **Feedback becomes repo-local context** — a doc update, a test, an ADR,
   a schema change, or an entry in `QUALITY.md`. No important decision
   stays only in chat.

This applies to both Claude Code and Codex. Chat-only decisions are not
durable; convert them into repo files before relying on them.

## Map of files in this directory

| File | Purpose |
| --- | --- |
| `README.md` (this file) | Workflow overview |
| `CONTEXT_BRIEF.md` | One-page repo snapshot for a fresh agent |
| `WORKFLOW.md` | Default agent loop |
| `PLANS.md` | ExecPlan convention |
| `TASK_TEMPLATE.md` | Template for small tasks |
| `HANDOFF.md` | Living state of the repo (most recently touched) |
| `QUALITY.md` | Known weak areas, debt, follow-ups |
| `EXTERNAL_READING_SUMMARY.md` | External principles applied here |
| `PROJECT_MAP.md` | Generated file map (regenerate with `scripts/update_context.py`) |
| `tasks/` | Per-task notes |
| `exec-plans/active/` | Active ExecPlans |
| `exec-plans/archive/` | Completed ExecPlans (move when done) |

## Conventions

- File names are lowercase-with-dashes for slugs and ALL_CAPS for the
  fixed-name documents above.
- Markdown only. No HTML, no images, no screenshots in repo.
- Keep individual files short and link laterally — context is scarce.
- Never put real secrets, tokens, paths to user media, or model artifacts
  in any of these files.
- Handoff is mandatory for both Claude Code and Codex before stopping work.
