# CODEX.md

Operating manual for OpenAI Codex in the Windows desktop app, CLI, or IDE.
Read `AGENTS.md` first; this file only adds Codex-specific local workflow.

## Start-here checklist

1. Open the repo root in Codex: `C:\Srtforge-lat\Srtforge` for this checkout.
2. Read `AGENTS.md`, `docs/agent/CONTEXT_BRIEF.md`,
   `docs/agent/HANDOFF.md`, and `docs/agent/WORKFLOW.md`.
3. Run:

   ```powershell
   git status --short
   git branch --show-current
   git log --oneline -10
   ```

4. Decide: small task -> `docs/agent/tasks/<slug>.md`; cross-cutting or
   resumable work -> ExecPlan in `docs/agent/exec-plans/active/`.
5. Before stopping, run the narrowest meaningful check, update
   `docs/agent/HANDOFF.md`, and record the exact next action.

## Local vs worktree

- Use the main local checkout for small, direct edits.
- Use Codex worktrees for parallel experiments, risky refactors, or changes
  that may be abandoned.
- In a worktree, do not assume `.venv`, `node_modules`, Cargo `target/`,
  generated binaries, model files, or media outputs exist. Recreate only the
  lightweight dependencies needed for the task.

## Windows-first commands

Activate the project virtualenv:

```powershell
.\.venv\Scripts\Activate.ps1
```

Run the lightweight harness:

```powershell
pwsh ./scripts/check.ps1
```

Refresh durable context:

```powershell
python scripts/update_context.py
python scripts/check_docs.py
```

Targeted checks:

```powershell
python -m pytest tests/test_worker_protocol.py tests/test_pipeline_events.py tests/test_srt_writer.py -q
cd srtforge-studio
pnpm exec tsc --noEmit
cd src-tauri
cargo check
```

Default checks must not require CUDA, model downloads, real media, cloud
APIs, FFmpeg-heavy processing, or secrets.

## Codex skills and local actions

- Repo-scoped Codex skills live in `.agents/skills/<skill-name>/SKILL.md`.
- Claude Code skills remain in `.claude/skills/`.
- Codex app local action scripts live in `.codex/actions/`; see
  `.codex/README.md`.

Use `.agents/skills/prime` at session start, `harness-check` before stopping
after code edits, `handoff` before ending any session, `limit-safe-stop` when
context or tooling looks risky, and `push-safe` only when the user explicitly
asks you to push.

## Small task vs ExecPlan

Use a task note from `docs/agent/TASK_TEMPLATE.md` for small, single-layer
work. Use an ExecPlan when work changes the worker protocol, touches Python +
Rust + TypeScript, changes CI or architecture, may exceed context/session
limits, or must be resumed by another agent. Follow `docs/agent/PLANS.md`.

## Handoff discipline

Before stopping, update `docs/agent/HANDOFF.md` with current branch, git
state, changed files, commands run, skipped checks, decisions, blockers, and
one concrete next recommended action. Chat-only decisions are not durable.

Near context or session limits:

1. Stop risky edits.
2. Run the narrowest meaningful check.
3. Update the active ExecPlan, if any.
4. Update `docs/agent/HANDOFF.md`.
5. Record changed files and commands.
6. Name the exact next command/action for the next agent.

## Safe Git push

Do not push unless the user explicitly asks. When pushing is requested, use the
normal `origin` remote, never an inline PAT URL, and never paste or store
tokens. If authentication fails, stop and ask the user to authenticate Git
Credential Manager or GitHub CLI outside Codex.
