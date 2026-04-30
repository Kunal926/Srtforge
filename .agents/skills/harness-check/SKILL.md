---
name: harness-check
description: Run the Windows lightweight Srtforge harness from Codex and report pass, fail, or skip accurately.
---

# harness-check

## When to use it

Use before stopping any session that touched code, contracts, checks, or
harness docs.

## Files to read

- `CODEX.md`
- `scripts/check.ps1`
- `docs/agent/HANDOFF.md`
- Active ExecPlan or task note, if one exists

## Exact Windows PowerShell commands

```powershell
.\.codex\actions\harness-check.ps1
```

If `.venv` is missing, report that clearly and do not fake a pass.

## Acceptance output

Report:

- harness command run,
- executed checks that passed,
- skipped checks and the printed reason,
- failed checks and first useful error,
- final exit code.

## What not to do

- Do not run CUDA, model, real-media, secret, cloud, or FFmpeg-heavy checks.
- Do not claim the harness passed unless it exited zero.
- Do not edit `scripts/check.ps1` only to make a failure disappear.

## How to update `docs/agent/HANDOFF.md`

Add the harness command and result under "Commands run and results"; add any
skips under "Skipped checks and why".

## Failure behavior

Use `fix-loop` on the first failing check. If PowerShell or `.venv` is
unavailable, record the environment blocker and run the narrowest equivalent
Python checks that are available.
