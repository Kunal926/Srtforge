# ADR 0001: Agent-generated development

- **Status:** Accepted
- **Date:** 2026-04-29
- **Deciders:** Srtforge owner + Claude Code

## Context

Srtforge is a small team / single-maintainer project with a wide surface:
a Python ML pipeline, two GUI front-ends (PySide6 and Tauri 2 + React), a
Rust shell, a Sonarr integration, and Windows packaging. Most coding work
is repetitive in shape (add a setting, plumb it through Python + Rust +
TypeScript, write tests, document) but high in volume.

Manual development against this surface is slow and error-prone. Coding
agents can do most of this work given the right scaffolding, but they
amplify whatever patterns they find in the repo. A repo that is messy in
its operating procedures will produce messy agent-generated code.

## Decision

We optimize the repository for **agent-generated development**:

1. The human owner provides intent, product judgment, review, and
   permissions. The human does not write code by default.
2. Coding agents implement, test, document, refactor, and validate all
   changes. When stuck, they extend the harness rather than asking the
   human to write code.
3. **Repo files are the system of record.** Decisions, contracts, plans,
   handoffs, and known weak areas live in versioned Markdown, not chat.
4. **The default validation harness is fast and self-contained.** It runs
   without CUDA, real models, real media, private secrets, or cloud calls.
   Heavy tests are marked and skipped by default.
5. **Worker / pipeline contracts are executable.** A protocol contract has
   a prose doc, a JSON schema (where practical), Python typed helpers, and
   tests on each side that catch drift.
6. **Failure becomes harness.** Every recurring failure produces a test,
   a doc, an ADR, a script, or a row in `docs/agent/QUALITY.md` — never
   just a fix.

## Consequences

### Positive

- A fresh agent can read `AGENTS.md`, `CONTEXT_BRIEF.md`, and `HANDOFF.md`
  and continue work without chat history.
- Multi-hour and cross-cutting work is captured in self-contained
  ExecPlans, surviving context resets.
- Skills under `.claude/skills/` standardize the most common operations
  (prime, plan, fix-loop, harness-check, handoff, pr-summary,
  protocol-change).
- CI catches regressions on the lightweight harness without needing GPUs
  or model downloads.

### Negative / trade-offs

- The repo carries more documentation overhead. New patterns must be
  documented, not just implemented.
- Heavy / integration tests need explicit markers and a separate run
  recipe. We accept that "the lightweight harness is green" is not a
  full proof of correctness for ML behavior.
- Agents must adhere to the operating discipline (handoff, ExecPlans,
  failure-domain rule). The human owner reviews adherence in PRs.

### Follow-up work this implies

- Future cross-cutting changes should write an ExecPlan rather than a
  single commit message novel.
- New worker events / actions must update Python emitter, Rust forwarder,
  TypeScript consumer, contract doc, schema, and test in lockstep.
- The fast harness must stay fast — if a test takes more than a few
  seconds and depends on a heavy resource, it should be marked.

## Alternatives considered

- **Keep a single sprawling CLAUDE.md as the only operating manual.**
  Rejected: it grows unbounded, rots quickly, and consumes context window
  on every session.
- **Push everything to chat / PRs.** Rejected: PRs are not searchable from
  inside the agent, and chat does not survive resets.
- **Heavyweight CI that runs the real pipeline on a GPU runner.**
  Rejected for the default path: we accept it as an aspirational nightly
  job, but the default agent loop must work without it.

## Related

- `AGENTS.md` (entry point)
- `docs/agent/WORKFLOW.md` (the loop)
- `docs/agent/PLANS.md` (ExecPlan convention)
- `docs/contracts/worker-protocol.md` (executable contract example)
- `.claude/skills/` (operating procedures)
