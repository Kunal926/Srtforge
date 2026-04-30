---
name: protocol-change
description: Make worker protocol changes in lockstep across docs, schemas, Python, Rust, TypeScript, and tests.
---

# protocol-change

## When to use it

Use for any add, remove, rename, shape change, or ordering change in worker
requests or events.

## Files to read

- `docs/contracts/worker-protocol.md`
- `docs/contracts/worker-events.schema.json`
- `docs/contracts/worker-requests.schema.json`
- `srtforge/worker_protocol.py`
- `srtforge/cli.py`
- `srtforge/logging.py`
- `srtforge/pipeline.py`
- `srtforge-studio/src/types.ts`
- `srtforge-studio/src/store.ts`
- `srtforge-studio/src-tauri/src/lib.rs`
- `tests/test_worker_protocol.py`
- `tests/test_cli_worker.py`
- `tests/test_pipeline_events.py`

## Exact Windows PowerShell commands

```powershell
python -m pytest tests/test_worker_protocol.py tests/test_cli_worker.py tests/test_pipeline_events.py -q
cd srtforge-studio
pnpm exec tsc --noEmit
cd src-tauri
cargo check
cd ..\..
python scripts/check_docs.py
.\.codex\actions\harness-check.ps1
```

## Acceptance output

- Contract prose and JSON schemas match.
- Python builders/parsers and emitters match the contract.
- TypeScript event/request types and reducer match the contract.
- Rust request/forwarding code compiles.
- Protocol tests cover the changed shape or ordering.

## What not to do

- Do not make protocol changes without an ExecPlan.
- Do not remove a field silently; deprecate first when possible.
- Do not let schemas lag prose docs.
- Do not put secrets in worker JSON.
- Do not skip tests because "the UI ignores it".

## How to update `docs/agent/HANDOFF.md`

Record each layer touched, protocol checks run, skipped checks and why, and
the next lockstep file still needing work.

## Failure behavior

If any layer cannot be updated, stop and mark the protocol change incomplete
in the active ExecPlan and handoff. Do not leave the repo claiming the new
contract is implemented.
