# HANDOFF.md

Living state of the repo. **Update this file before stopping any session.**

A fresh agent should be able to read this and `CONTEXT_BRIEF.md` and pick up
where you left off.

---

## Current goal

Persist Studio live debug/Typer logs and fix the Codex harness PowerShell
fallback.

Status: complete. Finished History rows now carry:

- `performance_log_path` for the existing Python `RunLogger` timing log.
- `debug_log_path` for the Tauri-captured live Studio debug log under
  `logs/studio-debug/*.debug.log`.

The protocol ExecPlan was completed and archived at
`docs/agent/exec-plans/archive/studio-live-logs.md`.

The Codex harness action now prefers `pwsh` and falls back to Windows
PowerShell when PowerShell 7 is not installed.

## Current branch / git state

- Branch: `codex-windows-agent-layer`.
- Latest commit: `4a7fcf7 codex: add Windows app operating layer`.
- Worktree has uncommitted changes. It includes this Studio debug-log work,
  the harness fallback fix, and the earlier completed progress-event work from
  the previous session.
- Pre-existing unrelated uncommitted files still left untouched:
  `srtforge-studio/src-tauri/Cargo.toml` and untracked
  `srtforge-studio/pnpm-lock.yaml`.
- No commit and no push done this session.

## Changed files

Studio debug/performance log work:

- `srtforge/pipeline.py`
- `srtforge/cli.py`
- `srtforge/worker_protocol.py`
- `srtforge-studio/src-tauri/src/lib.rs`
- `srtforge-studio/src/components/Queue.tsx`
- `srtforge-studio/src/lib/locate.ts`
- `srtforge-studio/src/store.ts`
- `srtforge-studio/src/types.ts`
- `srtforge-studio/src/styles/index.css`
- `tests/test_cli_worker.py`
- `tests/test_worker_protocol.py`
- `docs/contracts/worker-protocol.md`
- `docs/contracts/worker-events.schema.json`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/architecture/ARCHITECTURE.md`
- `docs/agent/PROJECT_MAP.md`
- `docs/agent/exec-plans/archive/studio-live-logs.md`
- `docs/agent/HANDOFF.md`

Harness fallback fix:

- `AGENTS.md`
- `CODEX.md`
- `.codex/actions/harness-check.ps1`
- `.codex/README.md`
- `.agents/skills/harness-check/SKILL.md`
- `.agents/skills/protocol-change/SKILL.md`
- `docs/agent/WORKFLOW.md`
- `docs/agent/TASK_TEMPLATE.md`
- `scripts/check.ps1`

Earlier progress-event uncommitted files still present:

- `srtforge/logging.py`
- `srtforge/engine_whisper.py`
- `srtforge/engine_parakeet.py`
- `srtforge/post/srt_utils.py`
- `tests/test_pipeline.py`
- `tests/test_pipeline_events.py`
- `docs/agent/QUALITY.md`
- `docs/agent/exec-plans/archive/progress-events.md`

## Commands run and results

- `.\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q` - pass; 36 passed.
- `cargo fmt --check` from `srtforge-studio/src-tauri/` - initially failed because `lib.rs` needed formatting.
- `cargo fmt` from `srtforge-studio/src-tauri/` - pass; formatted Rust code.
- `cargo test debug_log --lib` from `srtforge-studio/src-tauri/` - pass; 3 passed.
- `cargo check` from `srtforge-studio/src-tauri/` - pass.
- `pnpm exec tsc --noEmit` from `srtforge-studio/` - pass.
- `.\.venv\Scripts\python.exe scripts\update_context.py` - pass; regenerated `docs/agent/PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts\check_docs.py` - pass; `docs check OK`.
- `pwsh ./scripts/check.ps1` - fail; `pwsh` is not on PATH in this environment.
- `. .\.venv\Scripts\Activate.ps1; & .\scripts\check.ps1` from Windows PowerShell 5.1 - pass. Executed checks passed: Python import smoke, default pytest (`78 passed`), CLI smoke, Ruff, doc freshness, frontend type-check, and Rust `cargo check`.
- `cargo fmt --check` from `srtforge-studio/src-tauri/` after formatting - pass.
- `.\.codex\actions\harness-check.ps1` - initially failed after the fallback
  worked because `CODEX.md` no longer mentioned `scripts/check.ps1`, which
  `scripts/check_docs.py` requires.
- `.\.codex\actions\harness-check.ps1` after restoring the docs reference -
  pass. It printed `pwsh not found; falling back to Windows PowerShell.` and
  all executed checks passed, including default pytest (`78 passed`).
- `.\.venv\Scripts\python.exe scripts\check_docs.py` after the harness-doc
  updates - pass; `docs check OK`.

## Skipped checks and why

- No lightweight harness checks were skipped by the Windows PowerShell fallback
  run.
- `pwsh` is still not installed globally, but the repo-side Codex harness no
  longer depends on it.

## Decisions made

- Used the required `protocol-change` and `execplan` workflows because the work
  changes worker event metadata across Python, Rust, TypeScript, docs, and tests.
- Kept two log concepts distinct:
  `performance_log_path` is the Python pipeline timing log, while
  `debug_log_path` is the Studio live/Typer debug log.
- Captured debug logs in the Tauri shell, because it sees the same stdout/stderr
  stream that feeds Studio's Active live-log pane.
- Persisted only completed/failed History rows in Zustand, not queued or active
  rows, so app restart does not resurrect in-flight jobs.
- Deferred flagging/report bundles per user direction; this pass is logs only.
- Fixed the `pwsh` issue at the repo harness layer instead of installing global
  PowerShell 7: `.codex/actions/harness-check.ps1` now uses `pwsh` if present
  and `powershell.exe` otherwise.

## Known blockers

- `rg.exe` still returns access denied in this environment; use PowerShell
  `Select-String` fallback.
- Existing unrelated uncommitted Studio files remain:
  `srtforge-studio/src-tauri/Cargo.toml` and untracked
  `srtforge-studio/pnpm-lock.yaml`.

## Next recommended action

Review the combined progress-event and Studio log-persistence changes, then
commit them together or split them into two commits before pushing.
