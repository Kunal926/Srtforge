# Task: codex-windows-agent-layer

## Objective

Add a Codex-native Windows operating layer while preserving the existing
Claude Code layer and the already-dirty product worktree.

## Non-goals

- Do not rewrite product code.
- Do not redesign either GUI.
- Do not remove `CLAUDE.md` or `.claude/skills/`.
- Do not add default checks that require CUDA, real models, real media,
  cloud APIs, secrets, model downloads, or FFmpeg-heavy processing.
- Do not push.

## User-visible outcome

Codex can open the repo locally, use `CODEX.md`, repo-scoped skills in
`.agents/skills/`, safe local actions in `.codex/actions/`, and doc checks
that enforce the Codex operating layer.

## Starting git state

Branch at start: `agent-harness-engineering`.

New branch created for this work: `codex-windows-agent-layer`.

The working tree was already dirty before this task began. Those changes are
preserved and were not overwritten. Initial `git status --short` output:

```text
 M LICENSE
 M MAD.md
 M README.md
 M install.ps1
 M install.sh
 M models/voc_gabox.yaml
 M packaging/windows/srtforge_gui.spec
 M pyproject.toml
 M requirements.txt
 M srtforge-studio/.gitignore
 M srtforge-studio/README.md
 M srtforge-studio/index.html
 M srtforge-studio/package.json
 M srtforge-studio/packaging/windows/srtforge_worker.spec
 M srtforge-studio/packaging/windows/srtforge_worker_entry.py
 M srtforge-studio/src-tauri/Cargo.lock
 M srtforge-studio/src-tauri/Cargo.toml
 M srtforge-studio/src-tauri/build.rs
 M srtforge-studio/src-tauri/capabilities/default.json
 M srtforge-studio/src-tauri/icons/README.md
 M srtforge-studio/src-tauri/icons/android/mipmap-anydpi-v26/ic_launcher.xml
 M srtforge-studio/src-tauri/icons/android/values/ic_launcher_background.xml
 M srtforge-studio/src-tauri/src/lib.rs
 M srtforge-studio/src-tauri/src/main.rs
 M srtforge-studio/src-tauri/tauri.conf.json
 M srtforge-studio/src/App.tsx
 M srtforge-studio/src/components/ActiveDetail.tsx
 M srtforge-studio/src/components/BrandMark.tsx
 M srtforge-studio/src/components/EmptyState.tsx
 M srtforge-studio/src/components/History.tsx
 M srtforge-studio/src/components/Queue.tsx
 M srtforge-studio/src/components/Sidebar.tsx
 M srtforge-studio/src/components/StatusBar.tsx
 M srtforge-studio/src/components/TitleBar.tsx
 M srtforge-studio/src/icons.tsx
 M srtforge-studio/src/lib/locate.ts
 M srtforge-studio/src/lib/stages.ts
 M srtforge-studio/src/lib/tauri.ts
 M srtforge-studio/src/lib/workerConfig.ts
 M srtforge-studio/src/main.tsx
 M srtforge-studio/src/store.ts
 M srtforge-studio/src/styles/index.css
 M srtforge-studio/src/types.ts
 M srtforge-studio/tsconfig.json
 M srtforge-studio/tsconfig.node.json
 M srtforge-studio/vite.config.ts
 M srtforge/__init__.py
 M srtforge/__main__.py
 M srtforge/asr/__init__.py
 M srtforge/asr/_nemo_compat.py
 M srtforge/assets/__init__.py
 M srtforge/assets/images/__init__.py
 M srtforge/assets/styles/__init__.py
 M srtforge/assets/styles/win11.qss
 M srtforge/cli.py
 M srtforge/config.py
 M srtforge/config.yaml
 M srtforge/engine_events.py
 M srtforge/engine_parakeet.py
 M srtforge/engine_whisper.py
 M srtforge/ffmpeg.py
 M srtforge/gui_app.py
 M srtforge/logging.py
 M srtforge/pipeline.py
 M srtforge/post/__init__.py
 M srtforge/post/segmenter.py
 M srtforge/post/srt_utils.py
 M srtforge/settings.py
 M srtforge/sonarr_hook.py
 M srtforge/utils.py
 M srtforge/whisper.py
 M srtforge/win11_backdrop.py
 M tests/conftest.py
 M tests/test_cli_worker.py
 M tests/test_engine_parakeet.py
 M tests/test_ffmpeg.py
 M tests/test_nemo_compat.py
 M tests/test_pipeline.py
 M tests/test_settings.py
 M tests/test_sonarr_hook.py
?? srtforge-studio/pnpm-lock.yaml
```

## Files likely involved

- `CODEX.md` — Codex Windows operating manual.
- `.agents/skills/*/SKILL.md` — Codex repo-scoped skills.
- `.codex/` — Codex app local actions.
- `AGENTS.md`, `docs/agent/*` — shared multi-agent docs.
- `scripts/check_docs.py`, `scripts/update_context.py` — validation and map
  support for Codex files.
- `scripts/codex_prime.py` — optional read-only startup helper.

## Acceptance criteria

- `python scripts/check_docs.py` verifies the Codex operating layer.
- `docs/agent/HANDOFF.md` records this task, branch, checks, and next action.
- Existing Claude Code files remain intact.
- No secrets or token-bearing remote URLs are introduced.

## Commands to run

```powershell
python scripts/update_context.py
python scripts/check_docs.py
python -m pytest tests/test_worker_protocol.py tests/test_pipeline_events.py tests/test_srt_writer.py -q
python -m pytest --color=no -q
.\.venv\Scripts\Activate.ps1
pwsh ./scripts/check.ps1
```

Run frontend and Rust checks only if dependencies/tools exist:

```powershell
cd srtforge-studio
pnpm exec tsc --noEmit
cd src-tauri
cargo check
```

## Risks

- Accidental inclusion of local secrets or generated artifacts in agent docs.
- Doc checks could become too strict and block unrelated work.
- PowerShell may be unavailable from WSL even though the Windows app can run
  the `.ps1` scripts.

## Rollback notes

Use `git revert <commit>` after this task is committed. No generated binaries,
models, media, or dependency folders should be created by this task.

## Handoff update requirement

Update `docs/agent/HANDOFF.md` with commands run, skipped checks and why,
decisions, blockers, and one next recommended action.
