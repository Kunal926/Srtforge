---
name: protocol-change
description: Walk through a worker-protocol change across Python emitter, Rust forwarder, TypeScript consumer, contract docs, schemas, and tests. Use this whenever you add, rename, remove, or change the shape of a worker request or event.
disable-model-invocation: true
---

# protocol-change

The Srtforge worker protocol is the bridge between the Python pipeline
and both GUIs. **Every protocol change must update all sides in
lockstep**, otherwise the GUIs silently drift and the next failure is
mysterious.

This skill is `disable-model-invocation: true` — invoke it deliberately
when you know the change is a protocol-level edit.

## What counts as a protocol change

- Adding, renaming, removing, or reshaping any field in:
  - a request consumed by `srtforge worker` (`action: transcribe |
    normalize | separate | shutdown | clear_gpu_cache`),
  - an event emitted by `srtforge worker`.
- Adding a new event type or request action.
- Changing the order in which events are emitted for a given action.

If you're unsure, treat it as a protocol change and run this skill.

## Lockstep checklist

For each change, update all of:

1. **Python emitter** — `srtforge/cli.py` (worker loop) and/or
   `srtforge/logging.py` (`_emit_stage`). For new structured events,
   add a builder helper in `srtforge/worker_protocol.py`.
2. **Rust forwarder** — `srtforge-studio/src-tauri/src/lib.rs`. The
   forwarder is mostly transparent (it forwards JSON verbatim), but
   new `WorkerRequest` variants must be added to the enum and the
   `invoke_handler!` registration.
3. **TypeScript types** — `srtforge-studio/src/types.ts`. Update the
   `WorkerEvent` discriminated union and any new payload types.
4. **TypeScript reducer** — `srtforge-studio/src/store.ts`. Add a
   handler branch for new events, update existing branches when fields
   change shape.
5. **Tauri invoke wrapper** — `srtforge-studio/src/lib/tauri.ts` for new
   request actions.
6. **Contract doc** — `docs/contracts/worker-protocol.md`. Add the new
   request/event to the appropriate section with an example, required
   vs optional fields, and emitting/consuming sides.
7. **JSON schema** — `docs/contracts/worker-events.schema.json` and/or
   `docs/contracts/worker-requests.schema.json`. Keep it in sync with
   the prose doc.
8. **Python contract test** — `tests/test_worker_protocol.py` and
   `tests/test_cli_worker.py`. Add a test that asserts the new event
   is emitted with the documented shape, or that the new request is
   rejected/honored as documented.

## Steps

1. Read `docs/contracts/worker-protocol.md` to find the right section
   for the change.
2. Edit the contract doc first — defining the shape forces clarity.
3. Update the JSON schema to match.
4. Edit the Python side. Re-run
   `python -m pytest tests/test_cli_worker.py tests/test_worker_protocol.py`.
5. Edit the TypeScript types and store reducer. Re-run
   `cd srtforge-studio && pnpm exec tsc --noEmit`.
6. Edit the Rust side if a new request/event variant requires it.
   Re-run `cd srtforge-studio/src-tauri && cargo check`.
7. Run the broad harness: `pwsh ./scripts/check.ps1`.
8. Update the HANDOFF and (if relevant) PROJECT_MAP.

## Constraints

- Never deprecate a field by removing it silently — keep the field for
  one release, mark it deprecated in the contract doc, and add a
  follow-up entry to `docs/agent/QUALITY.md`.
- Never let the schema lag the prose doc. The protocol-sync test will
  flag drift.
- Never add a real-secret-bearing field to the protocol. Worker JSON
  is logged on stderr in some debug paths.
- Never skip the test for "I'll add it later." The test is the
  contract.
