---
name: prime
description: Read compact Srtforge context and summarize current state at session start. Use this before doing any other work in a fresh session.
---

# prime

Reads the compact context layer and reports back what you found. **Run
this first in every fresh session.**

## Steps

1. Read `AGENTS.md` to understand entry-point conventions and the links
   to deeper docs.
2. Read `docs/agent/CONTEXT_BRIEF.md` for a one-page repo snapshot.
3. Read `docs/agent/HANDOFF.md` for the previous session's state.
4. Run `git status --short` to see uncommitted work.
5. Run `git log --oneline -10` to see recent commits.
6. Run `git branch --show-current` to identify the active branch.
7. If `docs/agent/exec-plans/active/` contains any non-README `*.md`
   files, read each — these are active ExecPlans the previous session
   was driving.

## Report back

After reading, summarize:

- The project in one sentence.
- The active branch and recent commits.
- Whether the working tree is clean or dirty (and which files).
- The previous session's "next recommended action" from `HANDOFF.md`.
- Any active ExecPlans and their progress checkbox status.
- Anything in the brief that looks **wrong** for the current state
  (e.g., the brief mentions a feature that no longer exists). Flag
  these as candidates for harness updates.

## Constraints

- Do not modify any files.
- Do not run commands beyond `git status / log / branch`.
- Keep the summary under ~200 words. The user reads this.

## When this skill is the wrong choice

- You already primed earlier in this session — re-read targeted files
  with `Read` instead.
- You need to plan a specific change — use `plan` (or `execplan` for
  multi-hour work) after priming.
