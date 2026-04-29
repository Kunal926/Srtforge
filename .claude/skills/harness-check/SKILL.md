---
name: harness-check
description: Run the lightweight Srtforge validation harness and report what passed, failed, and was skipped. Run this before stopping any session that touched code.
disable-model-invocation: true
---

# harness-check

Runs `scripts/check.ps1` (Windows) or `scripts/check.sh` (Unix/WSL).
The harness is non-destructive and explicitly does not require CUDA,
real model files, real media, or private secrets.

## Steps

1. From the repo root, detect the host OS.
2. On Windows, run:

   ```powershell
   pwsh ./scripts/check.ps1
   ```

3. On Unix/WSL, run:

   ```bash
   bash ./scripts/check.sh
   ```

4. Read the script's output. The script reports each check as `OK`,
   `SKIPPED` (with reason), or `FAILED`.

## Report back

Summarize for the user:

- What ran and passed.
- What was skipped and why (each line).
- What failed (with the failing command and the first useful line of
  the error).
- Whether the harness exited zero.

## When the harness fails

Run the `fix-loop` skill against the first failing check. Do not
continue with later phases of work until the harness is green or the
user explicitly accepts the failure.

## Constraints

- Do not run heavy / model / GPU / FFmpeg-dependent tests as part of
  this skill. Those have their own markers and are excluded by the
  default pytest selection.
- Do not modify the harness scripts to make checks pass — fix the
  underlying problem or use `fix-loop`.
- This skill has `disable-model-invocation: true` because running the
  harness has filesystem side effects (writes `docs/agent/PROJECT_MAP.md`
  via `check_docs.py`'s implicit dependency on freshness).
