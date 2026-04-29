# .codex/

Project-local helpers for the Codex Windows app's local environment/actions.

These scripts are safe and non-destructive. They assume the project `.venv`
already exists. They do not install dependencies, download models, delete
outputs, call cloud APIs, or push to Git.

Use `actions/harness-check.ps1` before stopping a coding session that touched
code or harness behavior.

## Actions

- `actions/harness-check.ps1` — activate `.venv` and run
  `pwsh ./scripts/check.ps1`.
- `actions/doctor.ps1` — activate `.venv` and run `python scripts/doctor.py`.
- `actions/update-context.ps1` — regenerate `docs/agent/PROJECT_MAP.md` and
  run `python scripts/check_docs.py`.
- `actions/pytest-default.ps1` — run the default lightweight pytest
  selection.
- `actions/frontend-check.ps1` — run `pnpm exec tsc --noEmit` only when
  `srtforge-studio/node_modules` and `pnpm` already exist.
- `actions/cargo-check.ps1` — run `cargo check` only when Cargo and the Tauri
  project already exist.

No formal Codex project config file was present in this checkout when this
folder was created. If the Codex app later generates a shareable project
config, add these actions through the app settings and check in only the
generated config intended for the repo.
