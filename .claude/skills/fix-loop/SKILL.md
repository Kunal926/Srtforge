---
name: fix-loop
description: Run a failing command, diagnose root cause, patch, rerun, and record the failure domain. Use this when a check fails and you need to make it pass.
---

# fix-loop

Drives a tight read-error → diagnose → patch → rerun loop and records
the failure domain so the next agent doesn't repeat the investigation.

## Steps

1. **Run the failing command** the user (or the previous step)
   identified, capturing stdout + stderr.
2. **Read the error** carefully. Do not skim. The most common failure
   modes in Srtforge are:
   - Worker protocol drift (Python emits a field the TypeScript
     consumer doesn't know).
   - Pipeline missing optional config defaults.
   - PyInstaller bootloader issues on Windows.
   - Zombie `srtforge_worker.exe` locking the sidecar binary.
3. **Identify the failure domain.** Choose one:
   - missing test → add it,
   - missing contract → write it,
   - missing fixture → build a synthetic one,
   - missing doc → write it,
   - missing command/script → add it,
   - stale project map → regenerate it (`python scripts/update_context.py`),
   - unclear error → improve the error message or the script's
     diagnostics.
4. **Patch the smallest thing that resolves the root cause.** Do not
   change unrelated code. Do not catch+ignore the error — find the
   cause.
5. **Rerun the narrow command** to verify the fix.
6. **Run the broader lightweight check** (`pwsh ./scripts/check.ps1`)
   to verify no regressions.
7. **Record the failure domain** in `docs/agent/QUALITY.md` (if it
   represents a recurring class of failure) or in the relevant ADR /
   protocol doc.

## Constraints

- Do not retry a failing command in a sleep loop without a diagnosis.
- Do not increase test timeouts to mask flakiness — find the cause.
- Do not silently catch exceptions to make the test pass.
- Do not skip pre-commit hooks (`--no-verify`) without explicit user
  permission.
- Never delete or rename a test to make it pass.

## When this skill is the wrong choice

- The failure is a typo / one-line config mistake — just fix it
  inline with `Edit`.
- The user asked you to *plan* the fix, not execute it — use `plan`
  or `execplan` first.
