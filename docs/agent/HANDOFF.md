# HANDOFF.md

Living state of the repo. **Update this file before stopping any session.**

A fresh agent should be able to read this and `CONTEXT_BRIEF.md` and pick up
where you left off.

---

## Current goal

Add a Codex-native Windows app operating layer while preserving the existing
Claude Code layer and the pre-existing dirty worktree.

This session created `CODEX.md`, Codex repo skills under `.agents/skills/`,
safe local Codex action scripts under `.codex/actions/`, Codex doc checks,
and a read-only startup helper. The existing Claude Code layer remains in
`CLAUDE.md` and `.claude/skills/`.

## Current branch / git state

- Branch: `codex-windows-agent-layer`.
- Started from `agent-harness-engineering` with many pre-existing uncommitted
  product/test/doc changes. These were preserved and documented in
  `docs/agent/tasks/codex-windows-agent-layer.md`.
- This session's intended commit message:
  `codex: add Windows app operating layer`.
- No push done.
- `origin` remains `https://github.com/StiensGate928/Srtforge.git`; no inline
  PAT/token remote was added.

## Changed files

Codex layer added:

- `CODEX.md`
- `.agents/skills/{prime,plan,execplan,fix-loop,harness-check,handoff,pr-summary,protocol-change,repo-map,limit-safe-stop,cross-agent-review,push-safe}/SKILL.md`
- `.codex/README.md`
- `.codex/actions/{harness-check,doctor,update-context,pytest-default,frontend-check,cargo-check}.ps1`
- `docs/agent/tasks/codex-windows-agent-layer.md`
- `scripts/codex_prime.py`

Shared docs/checks updated:

- `AGENTS.md`
- `docs/agent/README.md`
- `docs/agent/CONTEXT_BRIEF.md`
- `docs/agent/WORKFLOW.md`
- `docs/agent/QUALITY.md`
- `docs/agent/HANDOFF.md`
- `docs/agent/PROJECT_MAP.md`
- `scripts/check_docs.py`
- `scripts/update_context.py`

Pre-existing dirty files outside this scope remain uncommitted unless they
were already dirty at task start; see the task note for the initial full
`git status --short` snapshot.

## Commands run and results

- `git status --short` from `/mnt/c/Srtforge-lat` — fail; directory was not a
  Git repo.
- `git branch --show-current` from `/mnt/c/Srtforge-lat` — fail; directory was
  not a Git repo.
- `git log --oneline -10` from `/mnt/c/Srtforge-lat` — fail; directory was not
  a Git repo.
- `git remote -v` from `/mnt/c/Srtforge-lat` — fail; directory was not a Git
  repo.
- `git status --short` from `/mnt/c/Srtforge-lat/Srtforge` — pass; showed a
  large pre-existing dirty tree.
- `git branch --show-current` — pass; initial branch
  `agent-harness-engineering`.
- `git log --oneline -10` — pass; latest commit
  `e6db215 agent harness: pin asr_engine in pipeline tests so CI is environment-independent`.
- `git remote -v` — pass; normal GitHub HTTPS origin, no token in URL.
- `git checkout -b codex-windows-agent-layer` — pass.
- `python scripts/update_context.py` — fail in this shell; `python` is not on
  PATH.
- `.\.venv\Scripts\python.exe scripts/update_context.py` — pass; regenerated
  `docs/agent/PROJECT_MAP.md`.
- `.\.venv\Scripts\python.exe scripts/check_docs.py` — pass; `docs check OK`.
- `.\.venv\Scripts\python.exe scripts/codex_prime.py` — pass after fixing
  Windows stdout encoding.
- `python scripts/check_docs.py` — fail in this shell; `python` is not on PATH.
- `python -m pytest tests/test_worker_protocol.py tests/test_pipeline_events.py tests/test_srt_writer.py -q` — fail in this shell; `python` is not on PATH.
- `python -m pytest --color=no -q` — fail in this shell; `python` is not on
  PATH.
- `.\.venv\Scripts\python.exe -m pytest tests/test_worker_protocol.py tests/test_pipeline_events.py tests/test_srt_writer.py -q` — pass; 35 passed.
- `.\.venv\Scripts\python.exe -m pytest --color=no -q` — pass; 74 passed.
- `.\.venv\Scripts\Activate.ps1` executed from Bash — fail; PowerShell script
  cannot be interpreted by Bash.
- `pwsh ./scripts/check.ps1` — fail; `pwsh` is not on PATH in this shell.
- `bash ./scripts/check.sh` — fail with system `/usr/bin/python3`; project
  dependencies such as `typer` are not installed there.
- `PATH="/tmp/codex-python-bin:$PATH" bash ./scripts/check.sh` with a
  temporary shim to `.venv\Scripts\python.exe` — pass; Python smoke, default
  pytest, Ruff, and doc freshness passed.
- `pnpm exec tsc --noEmit` from `srtforge-studio/` — fail; `pnpm` is not on
  PATH.
- `cargo check` from `srtforge-studio/src-tauri/` — fail; `cargo` is not on
  PATH.
- `rg -n "github_pat|ghp_|GEMINI_API_KEY|HF_TOKEN" CODEX.md .agents .codex docs/agent scripts/check_docs.py scripts/codex_prime.py AGENTS.md` — pass; only found the literal push-safety scan command in
  `.agents/skills/push-safe/SKILL.md`.

## Skipped checks and why

- Native PowerShell harness (`.\.venv\Scripts\Activate.ps1` then
  `pwsh ./scripts/check.ps1`) could not run from this WSL shell because
  `pwsh` is unavailable and `.ps1` activation cannot be sourced by Bash.
- Frontend type-check could not run even though `node_modules/` exists because
  `pnpm` is not on PATH.
- Rust `cargo check` could not run because `cargo` is not on PATH.
- `scripts/check.sh` with the system Python was not accepted as validation
  because it failed from missing project dependencies; the rerun with the
  project venv Python shim passed.

## Decisions made

- Codex skills live under `.agents/skills/`, not `.codex/skills/`.
- `.codex/` contains only conservative README/action scripts because no
  generated Codex app project config was present in this checkout.
- `.claude/skills/` and `CLAUDE.md` were preserved.
- Added `scripts/codex_prime.py` as a read-only startup helper.
- Extended `scripts/check_docs.py` to validate `CODEX.md`, required Codex
  skills, skill metadata, `.codex/README.md`, Codex action presence, and
  required cross-links.
- Tightened `scripts/check_docs.py` project-map staleness checks so generated
  `__pycache__/*.pyc` files do not make `PROJECT_MAP.md` look stale.

## Known blockers

- This Codex shell does not expose `pwsh`, `pnpm`, or `cargo` on PATH.
- The exact Windows PowerShell harness still needs to be run from a native
  PowerShell-capable environment or the Codex app action runner.
- The working tree still contains many pre-existing uncommitted changes from
  before this Codex task. They were intentionally not reverted.

## Next recommended action

From the Windows Codex app or a native PowerShell terminal, run
`.codex/actions/harness-check.ps1`; record the result in this handoff if it
differs from the WSL mirror harness result.
