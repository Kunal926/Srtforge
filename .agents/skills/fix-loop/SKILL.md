---
name: fix-loop
description: Diagnose a failing command, patch the root cause, rerun the narrow check, and record the failure domain.
---

# fix-loop

## When to use it

Use whenever a check, build, test, or doc validation command fails during
Codex work.

## Files to read

- The failing command output
- `docs/agent/WORKFLOW.md`
- `docs/agent/QUALITY.md`
- The smallest source, test, contract, or script files implicated by the
  failure
- Active ExecPlan or task note, if one exists

## Exact Windows PowerShell commands

Start by rerunning only the failing command, for example:

```powershell
python scripts/check_docs.py
python -m pytest tests/test_cli_worker.py -q
cd srtforge-studio
pnpm exec tsc --noEmit
```

After patching, rerun the same narrow command before any broad harness:

```powershell
python scripts/check_docs.py
```

## Acceptance output

- The root cause is named.
- The patch addresses the root cause rather than hiding the symptom.
- The original failing command is rerun and its result is recorded.
- The failure domain is recorded as test, contract, fixture, doc, script,
  dependency, environment, or product bug.

## What not to do

- Do not blindly retry.
- Do not hide failures by weakening checks.
- Do not skip the failing check without recording why.
- Do not ask the human to write code.

## How to update `docs/agent/HANDOFF.md`

Record the failing command, root cause, patch summary, rerun result, and any
remaining blocker or skipped check.

## Failure behavior

If you cannot fix the failure in-session, add a `docs/agent/QUALITY.md` entry
with evidence and proposed fix, then update handoff with the exact next
diagnostic command.
