---
name: repo-map
description: Refresh and validate the generated Srtforge project map after file layout changes.
---

# repo-map

## When to use it

Use after adding, removing, or moving files, directories, skills, docs,
contracts, scripts, or tests.

## Files to read

- `scripts/update_context.py`
- `scripts/check_docs.py`
- `docs/agent/PROJECT_MAP.md`
- `docs/agent/HANDOFF.md`

## Exact Windows PowerShell commands

```powershell
python scripts/update_context.py
python scripts/check_docs.py
```

## Acceptance output

- `docs/agent/PROJECT_MAP.md` is regenerated.
- `python scripts/check_docs.py` exits zero.
- Report whether `docs/agent/PROJECT_MAP.md` is fresh.

## What not to do

- Do not manually edit generated sections in `PROJECT_MAP.md`; update
  `scripts/update_context.py` instead.
- Do not include generated artifacts, model files, media, or dependency
  directories in the map.

## How to update `docs/agent/HANDOFF.md`

Record both commands and whether the project map was refreshed.

## Failure behavior

If map generation fails, record the exact error and do not claim docs are
fresh. If `check_docs.py` fails, use `fix-loop`.
