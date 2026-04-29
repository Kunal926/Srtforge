---
name: pr-summary
description: Produce a final PR / session summary in the canonical Srtforge format. Use this as the last step before stopping or before opening a pull request.
---

# pr-summary

Produces the final summary block in the canonical format Srtforge uses
for both PR descriptions and end-of-session summaries.

## Output format

Always exactly these sections, in this order:

```markdown
## Summary

<1–3 sentences describing what shipped and why>

## Changed files

- `<path>` — <what changed and why>
- ...

## Commands run

- `<command>` — pass/fail/skip and reason

## Harness improvements added

- <improvement>

## Known limitations

- <limitation, or "None known">

## Next recommended agent task

<one concrete next task, written so a fresh agent can act on it>
```

## Steps

1. Run `git status --short` and `git diff --stat` to enumerate
   changed files.
2. Re-read `docs/agent/HANDOFF.md` to align with the session state.
3. Group changed files by intent (docs, harness, code, tests) when
   listing them.
4. Under "Commands run", list each non-trivial check (`pytest`,
   `ruff`, `pwsh ./scripts/check.ps1`, `cargo check`, `pnpm tsc`) with
   its actual outcome — pass / fail / skip + reason. **Do not list a
   command as passed unless you actually ran it.**
5. Under "Harness improvements added", list any new tests, scripts,
   skills, contract docs, or schemas this session introduced.
6. Under "Next recommended agent task", state ONE thing — not a
   menu. Pick the highest-leverage follow-up.

## Constraints

- Never claim a check passed that you didn't run.
- Never hide failures.
- Keep the summary section to 1–3 sentences. Detail goes in the other
  sections.
- Do not dump full diffs into the summary — let the human read the
  diff in their tool.
