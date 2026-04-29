# AGENTS.md — Srtforge

This file is the entry point for any coding agent working in this repository.
Keep it short. Push detailed knowledge into the linked docs.

## What Srtforge is

Offline subtitle generator. **One Python pipeline (`srtforge/`)**, two GUIs:

- `srtforge/gui_app.py` — legacy PySide6 desktop GUI (`srtforge-gui`)
- `srtforge-studio/` — current Tauri 2 + React + Zustand GUI

Both GUIs spawn `srtforge worker` as a child process and communicate over a
JSON-per-line stdin/stdout protocol. The Python pipeline is the source of truth.

## Operating principles for this repo

1. **Humans steer; agents implement.** When stuck, do not ask the human to
   write code — identify the missing harness capability and add or propose it.
2. **Repo is the system of record.** Decisions, plans, contracts, and known
   debt live in files under `docs/`, not in chat.
3. **Default tests must not require CUDA, real models, real media, or
   secrets.** Slow/heavy tests are marked and skipped by default.
4. **Worker protocol changes touch all sides in lockstep:** Python emitter,
   Rust forwarder, TypeScript consumer, contract docs, tests.

## First files to read (in this order)

1. `docs/agent/CONTEXT_BRIEF.md` — one-page repo snapshot
2. `docs/agent/HANDOFF.md` — what the previous session left behind
3. `docs/agent/WORKFLOW.md` — the loop you should be running
4. `CLAUDE.md` or `CODEX.md` — agent-specific operating manual
5. `docs/architecture/ARCHITECTURE.md` — where things live and why

Then, when relevant:
- `docs/agent/PLANS.md` — when to write an ExecPlan
- `docs/contracts/worker-protocol.md` — JSON event/request shapes
- `docs/adr/` — decisions of record
- `docs/agent/QUALITY.md` — known weak areas / cleanup queue
- `docs/agent/EXTERNAL_READING_SUMMARY.md` — external principles applied here

## Agent-specific manuals

- Claude Code: `CLAUDE.md` and `.claude/skills/`
- OpenAI Codex Windows app / CLI / IDE: `CODEX.md` and `.agents/skills/`
- Codex app local actions: `.codex/`

## Canonical commands (Windows-first)

Activate the project venv before any Python work:

```powershell
.\.venv\Scripts\Activate.ps1
```

Default lightweight check (run this before stopping):

```powershell
pwsh ./scripts/check.ps1
```

On Unix/WSL:

```bash
bash ./scripts/check.sh
```

Narrow checks during a fix loop:

```powershell
python -m pytest -m "not slow and not requires_model and not requires_cuda and not requires_media and not requires_ffmpeg"
python -m pytest tests/test_cli_worker.py -k worker
ruff check srtforge tests
```

Tauri Studio:

```powershell
cd srtforge-studio
pnpm install        # one-time
pnpm tauri dev      # dev shell + Vite + bundled sidecar
```

The Python sidecar is **not** rebuilt by `pnpm tauri dev`. After any change
to `srtforge/` or the entry shim, follow the rebuild steps in `CLAUDE.md`.

## When to use an ExecPlan

Use an ExecPlan in `docs/agent/exec-plans/active/<slug>.md` when the work is:

- multi-hour,
- touches Python + Rust + TypeScript together,
- changes the worker protocol,
- changes CI,
- changes architectural boundaries,
- or otherwise needs to survive a context reset.

See `docs/agent/PLANS.md` for the required structure.

## Handoff requirement

**Every session must update `docs/agent/HANDOFF.md` before stopping.**
A fresh agent should be able to continue from repo files alone.

## Safety rules

- Never commit: `.venv/`, `node_modules/`, `target/`, `dist/`, `build/`,
  `models/`, `output/`, `tmp/`, log files, real media, model files, real
  secrets, or generated PyInstaller / Tauri bundle outputs.
- Never run `git push --force` against `main`.
- Never skip git hooks (`--no-verify`) unless the user asks for it.
- Never download models, fetch real media, or call cloud APIs from the
  default test/CI path.
- Never delete user media, generated SRTs, model files, configs, output
  dirs, or temp dirs.

## Failure-domain rule

If you get stuck, the missing thing is almost always a missing harness
capability: a missing test, contract, fixture, doc, command, or stale
project map. Add the missing piece (or file a tracked follow-up in
`docs/agent/QUALITY.md`) before walking away.

## Links

- Claude Code manual: `CLAUDE.md`
- Codex manual: `CODEX.md`
- Brief: `docs/agent/CONTEXT_BRIEF.md`
- Workflow: `docs/agent/WORKFLOW.md`
- Plans: `docs/agent/PLANS.md`
- Handoff: `docs/agent/HANDOFF.md`
- Architecture: `docs/architecture/ARCHITECTURE.md`
- Worker protocol: `docs/contracts/worker-protocol.md`
- ADRs: `docs/adr/`
- Quality / debt: `docs/agent/QUALITY.md`
- External reading: `docs/agent/EXTERNAL_READING_SUMMARY.md`
