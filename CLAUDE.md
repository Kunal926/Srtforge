# CLAUDE.md

Operating manual for Claude Code (claude.ai/code) working inside the Srtforge
repository. **Read `AGENTS.md` first** — this file assumes you have.

## Start here checklist

When you open a fresh session:

1. Read `AGENTS.md` (entry point).
2. Read `docs/agent/CONTEXT_BRIEF.md` (one-page repo snapshot).
3. Read `docs/agent/HANDOFF.md` (last session's state).
4. Run `git status --short` and `git log --oneline -10`.
5. Decide: small task → just plan; multi-hour or cross-cutting → write an
   ExecPlan in `docs/agent/exec-plans/active/`.
6. Run the lightweight check before stopping: `pwsh ./scripts/check.ps1`.
7. Update `docs/agent/HANDOFF.md` with what you did and what's next.

## Repo layout — two heads, one pipeline

This repo ships **one Python subtitle pipeline** consumed by **two GUIs**:

- `srtforge/` — pipeline package + Typer CLI (`srtforge run`, `srtforge series`,
  `srtforge worker`, `srtforge sonarr-hook`). Source of truth for transcription.
- `srtforge/gui_app.py` — legacy **PySide6** desktop wrapper, distributed as
  `srtforge-gui`. Shells out to a child `srtforge` CLI process via `subprocess`.
- `srtforge-studio/` — current **Tauri 2.0 + React + Zustand** wrapper. Tauri
  spawns a PyInstaller-bundled worker sidecar
  (`srtforge-studio/src-tauri/binaries/srtforge_worker-x86_64-pc-windows-msvc.exe`)
  and talks to it over the same JSON-per-line stdin/stdout protocol as the
  legacy GUI.

When porting new UI features, port from the original prototype at
`project/srtforge_studio/{watch,history,tools}.jsx` — that's the design source
of truth, not the legacy PySide6 layout.

Where things live in detail: `docs/architecture/ARCHITECTURE.md`. Generated
file map: `docs/agent/PROJECT_MAP.md` (regenerate via
`python scripts/update_context.py`).

## Canonical commands

Primary platform is Windows 11 / PowerShell. Linux/WSL is secondary.

### Daily Python work

```powershell
.\.venv\Scripts\Activate.ps1
srtforge --help                     # Typer CLI
srtforge run path\to\video.mkv      # single file end-to-end
srtforge worker                     # persistent JSON loop on stdin/stdout (used by both GUIs)
srtforge-gui                        # legacy PySide6 GUI
```

### Tests

Default fast tests (no GPU/models/media/FFmpeg):

```powershell
python -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg"
```

Single test:

```powershell
python -m pytest tests/test_cli_worker.py::test_worker_emits_srt_written_and_job_completed_on_success
```

Full lightweight harness (Python + Ruff + CLI smoke + frontend build if available):

```powershell
pwsh ./scripts/check.ps1
```

### Tauri Studio

```powershell
cd srtforge-studio
pnpm install            # one-time
pnpm tauri dev          # dev shell + Vite + bundled sidecar
```

`pnpm tauri dev` does **not** rebuild the Python sidecar. After any change
to `srtforge/`, the spec, or the entry shim, rebuild it manually:

```powershell
.\.venv\Scripts\Activate.ps1     # MUST be the project venv
pyinstaller --clean --noconfirm srtforge-studio\packaging\windows\srtforge_worker.spec --distpath srtforge-studio\src-tauri\binaries
ren srtforge-studio\src-tauri\binaries\srtforge_worker.exe srtforge_worker-x86_64-pc-windows-msvc.exe
```

If `pnpm tauri dev` fails with `tauri-build ... PermissionDenied "Access is
denied"`, it's almost always a zombie worker holding the sidecar `.exe`
open. Recover with:

```powershell
Get-Process srtforge_worker -ErrorAction SilentlyContinue | Stop-Process -Force
```

## Safe-edit boundaries

| Layer | Change here when … | Don't edit unless … |
| --- | --- | --- |
| `srtforge/pipeline.py` | adding/reordering pipeline stages | you've also updated `WorkerStage` in `srtforge-studio/src/types.ts` and the protocol doc |
| `srtforge/cli.py` `worker` | adding/changing worker actions/events | you've also updated `WorkerRequest` in `lib.rs`, `WorkerEvent` in `types.ts`, the `store.ts` reducer, and `docs/contracts/worker-protocol.md` |
| `srtforge/settings.py` | adding new settings | the new field has a sensible default and existing configs still load |
| `srtforge-studio/src-tauri/src/lib.rs` | adding Tauri commands | new commands are also wired into `srtforge-studio/src/lib/tauri.ts` |
| `srtforge-studio/src/store.ts` | reacting to new events | the new event is documented in the protocol contract |

## Worker protocol — the bridge

`srtforge worker` runs a persistent loop that consumes one JSON object per
stdin line and emits one JSON object per stdout line. Defined in
`srtforge/cli.py`. **Always edit all three sides plus the contract doc and
the test in lockstep:**

1. Python emitter: `srtforge/cli.py` (and `srtforge/logging.py` for stage events).
2. Rust forwarder: `srtforge-studio/src-tauri/src/lib.rs`.
3. TypeScript consumer: `srtforge-studio/src/types.ts` + `srtforge-studio/src/store.ts`.
4. Contract doc: `docs/contracts/worker-protocol.md`.
5. Test: `tests/test_cli_worker.py`.

Use the `protocol-change` skill (`.claude/skills/protocol-change/SKILL.md`)
to walk through this safely.

## When to write an ExecPlan

Multi-hour work, anything cross-cutting (Python + Rust + TS), protocol
changes, CI changes, architectural changes — write an ExecPlan in
`docs/agent/exec-plans/active/<slug>.md` per the structure in
`docs/agent/PLANS.md`. A small bug fix in one file does not need an
ExecPlan; a new event type does.

## Failure-domain discipline

When a check fails or you can't make progress, identify the missing harness
capability before retrying:

- missing test → add it,
- missing contract → write it,
- missing fixture → build a synthetic one,
- missing doc → write it,
- missing command/script → add it,
- stale project map → regenerate it (`python scripts/update_context.py`),
- unclear error → improve the error or the script's diagnostics.

If you can't add it in this session, file it in `docs/agent/QUALITY.md`.

## Test classification

Tests are marked with one of `unit`, `contract`, `integration`, `slow`,
`requires_ffmpeg`, `requires_model`, `requires_cuda`, `requires_media`.
The default selection in `pyproject.toml` excludes everything heavy. CI
runs only the lightweight default. **Do not** make a test require CUDA,
real models, real media, or private secrets unless it is explicitly marked
and skipped by default.

## Secrets

Never commit:
- `HF_TOKEN`, `GEMINI_API_KEY`, or any GitHub PAT,
- entries in `srtforge.config` that contain a real `api_key`,
- log files (they may contain paths or tokens),
- screenshots that show real keys.

Document required env vars in `.env.example`. The default test/CI path
must not require any of these to run.

## What CLAUDE.md does not know that you may need

- **Sonarr hook contract:** `srtforge/sonarr_hook.py` reads `EpisodeFile.Path`
  + `EventType` from env. Wired into Typer as `sonarr-hook`.
- **Settings drawer / Studio UI state:** parts of
  `srtforge-studio/src/components/settings/` are UI-only stubs against the
  real `srtforge.settings` YAML schema; check both sides before claiming a
  setting is wired up.
- **PyInstaller multiprocessing on Windows:** `pyi_rth_multiprocessing.py`
  runtime hook already calls `freeze_support()` — adding it manually to the
  entry shim is redundant and has historically broken the bootloader. If a
  child crashes with "Bootloader did not set sys._pyinstaller_pyz", do a
  `--clean` rebuild before assuming a multiprocessing bug.
- **CUDA / GPU constraints:** `cuda-python>=12.3,<13` is a hard pin; the
  Parakeet GPU path rejects 13+. NeMo's Megatron-microbatch shim lives in
  `srtforge.asr._nemo_compat`. PyInstaller spec uses `collect_all` and
  `module_collection_mode={"nemo": "pyz+py", "torch": "pyz+py"}` so
  TorchScript can find source at runtime.
