# HANDOFF.md

Living state of the repo. **Update this file before stopping any session.**

A fresh agent should be able to read this and `CONTEXT_BRIEF.md` and pick
up where you left off.

---

## Current goal

Convert Srtforge into an agent-generated, harness-engineered repository
per the spec consumed in this session. Done in 9 phases: compact
entrypoint docs, ExecPlan convention, durable context, fast validation
harness, worker/pipeline contract docs and tests, Claude Code skills,
architecture invariants, lint/CI gate.

## Current branch / git state

- Branch: `agent-harness-engineering`, branched from
  `srtforge-studio-bundle-fixes`.
- Pre-existing modification carried along: `srtforge-studio/src-tauri/Cargo.toml`
  (worker bundle fix from a previous session, not touched here).
- Working tree dirty with this session's work — see "Changed files".
- Not yet committed; no push done.

## Changed files

This session adds these new files:

- `AGENTS.md` — compact entry point.
- `.env.example` — placeholder env vars.
- `.github/workflows/harness.yml` — lightweight CI gate.
- `.claude/skills/{prime,plan,execplan,fix-loop,harness-check,handoff,pr-summary,protocol-change}/SKILL.md`
  — repo-local skills.
- `docs/agent/{README,CONTEXT_BRIEF,WORKFLOW,PLANS,HANDOFF,QUALITY,EXTERNAL_READING_SUMMARY,TASK_TEMPLATE,PROJECT_MAP}.md`
- `docs/agent/{tasks,exec-plans/active,exec-plans/archive}/README.md`
- `docs/architecture/ARCHITECTURE.md`
- `docs/contracts/worker-protocol.md`
- `docs/contracts/{worker-events,worker-requests}.schema.json`
- `docs/adr/{README,ADR_TEMPLATE,0001-agent-generated-development}.md`
- `scripts/{check.ps1,check.sh,doctor.py,update_context.py,check_docs.py}`
- `srtforge/worker_protocol.py` — typed parse helpers + event builders.
- `tests/{test_worker_protocol,test_pipeline_events,test_srt_writer}.py`
  — contract / golden tests.

Modifies these existing files:

- `CLAUDE.md` — rewritten as Claude-Code operating manual.
- `pyproject.toml` — adds pytest config (with markers) + ruff config +
  optional `[dev]` extras.
- `.gitignore` — expanded to cover generated artifacts (target, dist,
  egg-info, sidecar binaries, vendored ffmpeg, etc.).
- `srtforge/logging.py` — stage events now carry `msg` and `run_id`.
- `tests/test_settings.py` — fixed stale assert (default
  `subsampling_conv_chunking_factor` is `0`, not `1`).
- `tests/test_cli_worker.py` — switched the `_Settings` mock to a real
  `AppSettings()` so the test doesn't drift when `_build_pipeline_config`
  reads new fields.
- `tests/test_pipeline.py` — patches `write_srt` at the actual import
  site (`srtforge.post.srt_utils.write_srt` and
  `srtforge.pipeline._write_srt_with_diag`) instead of the legacy
  re-export.
- `srtforge/gui_app.py`, `srtforge/post/segmenter.py`,
  `srtforge/post/srt_utils.py` — ruff auto-fix removed unused imports.

## Commands run and results

- `git checkout -b agent-harness-engineering` — pass.
- `python scripts/update_context.py` — pass (writes
  `docs/agent/PROJECT_MAP.md`).
- `python scripts/check_docs.py` — pass (`docs check OK`).
- `python -m pytest --color=no -q` — **74 passed in 3.52s**.
- `python -m srtforge --help` — pass.
- `python -m ruff check srtforge tests scripts` — pass (`All checks
  passed!`).
- `pwsh ./scripts/check.ps1` — pass (all 7 executed checks green;
  Python import smoke, pytest, CLI smoke, ruff, doc freshness, frontend
  type-check, cargo check).

## Skipped checks and why

- All `slow`, `requires_ffmpeg`, `requires_model`, `requires_cuda`, and
  `requires_media` tests are skipped by default per the addopts in
  `pyproject.toml`. None of these markers are present in the existing
  suite yet — the markers exist for future heavy tests to opt out of
  the default selection.

## Decisions made

- **Default test selection** excludes slow / model / cuda / media /
  ffmpeg markers. CI runs only this default. Heavy tests, when added,
  must opt in via marker.
- **Ruff first-pass policy** is `select = ["F"]` (pyflakes only — real
  bugs). Style rules (`E`), import sorting (`I`), pyupgrade (`UP`), and
  bugbear (`B`) are intentionally OFF until the existing tree has been
  cleaned up. Future cleanup tracked in `docs/agent/QUALITY.md`.
- **CI installs the package without heavy ML deps** (`pip install -e .
  --no-deps` plus a small lightweight subset). Heavy deps
  (`audio-separator`, `cuda-python`, `faster-whisper`, NeMo, PySide6)
  are not needed in the default test path because tests monkeypatch
  around the engines.
- **Stage events carry `msg` and `run_id`** in addition to the existing
  `stage`, `state`, `seconds`, `ok` fields. Schema and contract doc
  updated; consumers can ignore unknown fields.
- **Three pre-existing test failures fixed** by updating the tests to
  match the current code (settings default, mock structure, write_srt
  patch site). All three were inherited from the parent branch as
  noted in the prior session's compaction summary.

## Known blockers

None for the current goal. CI is defined but not yet observed green on
GitHub Actions — the first push to this branch will tell us. The CI
job is conservative (lightweight subset only); if it fails, the most
likely cause is a transitive dep that the package's `import` path needs
that I didn't list in `runtime-deps`.

## Next recommended action

**Wire fine-grained `progress` events through the pipeline** so the
Studio's stage dots can advance smoothly inside long stages (ASR,
post-processing) instead of toggling at stage boundaries. The contract
already has the event shape (`docs/contracts/worker-protocol.md` →
`progress`), the `worker_protocol.progress_event` builder is in place,
and `tests/test_pipeline_events.py` provides the harness pattern to
extend with progress assertions. Use the `protocol-change` skill to
walk the lockstep edits; expect:

1. Add a `progress_callback` (or similar) to `PipelineConfig`.
2. Emit progress from inside the ASR loop and the post-processing
   batch loop.
3. Worker installs the per-job progress emitter the same way it
   already installs the stage emitter.
4. Extend `test_pipeline_events.py::TestStageEventFlow` with a
   `TestProgressMonotonic` class.
5. Update `srtforge-studio/src/store.ts` to consume the events
   (already typed, just needs handler logic).

After that, in priority order:

- **Graceful Tauri-shell shutdown** — send `{"action":"shutdown"}` to
  the worker on `RunEvent::ExitRequested`, preventing the
  zombie-`srtforge_worker.exe` PermissionDenied panic on the next
  `pnpm tauri dev`.
- **Real Sonarr webhook listener** for the Watch view (currently
  UI-only).
- **Real GPU/VRAM telemetry probe** in the Rust shell (placeholder
  `gpuPct={0}` / `vram="—"` in the Studio sidebar).
