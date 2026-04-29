---
name: handoff
description: Update docs/agent/HANDOFF.md before stopping a session, so the next agent can continue from repo files alone. Run this last in every coding session.
disable-model-invocation: true
---

# handoff

Updates `docs/agent/HANDOFF.md` so the next session has everything it
needs to continue.

## Required sections (matched by `scripts/check_docs.py`)

The handoff file must contain these `##` headings:

- Current goal
- Current branch / git state
- Changed files
- Commands run and results
- Skipped checks and why
- Decisions made
- Known blockers
- Next recommended action

## Steps

1. Run `git status --short` and `git log --oneline -5` to capture
   current state.
2. Read existing `docs/agent/HANDOFF.md` to identify what to preserve
   vs. replace. Past handoffs are not a journal — overwrite stale
   sections rather than appending.
3. Update each required section:
   - **Current goal:** what the session was trying to do.
   - **Current branch / git state:** branch name, ahead-of count,
     uncommitted files (counts, not full paths).
   - **Changed files:** grouped logically with one-line "why".
   - **Commands run and results:** narrow checks, the broad
     `scripts/check.ps1` result, any heavy tests run on demand.
   - **Skipped checks and why:** each entry from the harness output's
     "SKIPPED" lines.
   - **Decisions made:** anything that affects future sessions.
   - **Known blockers:** what would stop the next agent.
   - **Next recommended action:** one concrete next task, written so
     a fresh agent can act on it.

4. If the file layout changed (new directories, moved files),
   regenerate the project map:

   ```powershell
   python scripts/update_context.py
   ```

5. If the architecture / pipeline shape changed, also update
   `docs/agent/CONTEXT_BRIEF.md`. Keep it under one page.

6. Run `python scripts/check_docs.py` to verify all required sections
   are present and `AGENTS.md` links resolve.

## Constraints

- Do not put real secrets, real media paths, or model checkpoint
  contents in the handoff.
- Do not log full command outputs in the handoff — link to log files
  or the relevant test name.
- Keep the file under ~150 lines.
