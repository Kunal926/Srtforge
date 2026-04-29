#!/usr/bin/env python
"""Generate ``docs/agent/PROJECT_MAP.md`` from the current repo layout.

Idempotent and read-only outside of the single output file. Writes a
compact tree plus a list of known-key files. Skips heavy artifacts
(``.venv/``, ``node_modules/``, ``target/``, ``models/``, ``logs/``,
generated bundles, media, etc.) so the map stays useful for agents.

Run:

    python scripts/update_context.py

Exits non-zero only on filesystem errors. The script never deletes,
moves, or rewrites anything other than ``docs/agent/PROJECT_MAP.md``.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "docs" / "agent" / "PROJECT_MAP.md"

# Directory names the map should never recurse into (matched anywhere).
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        "target",
        "models",
        "tmp",
        "output",
        "logs",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".idea",
        ".vscode",
        "gen",
        "out",
    }
)

# File extensions / suffixes that should not appear in the map.
SKIP_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyd",
        ".dll",
        ".so",
        ".dylib",
        ".exe",
        ".lib",
        ".a",
        ".o",
        ".obj",
        ".log",
        ".onnx",
        ".ckpt",
        ".pt",
        ".bin",
        ".mkv",
        ".mp4",
        ".webm",
        ".mp3",
        ".wav",
        ".flac",
        ".srt",
        ".lock",
    }
)

# Filenames that are noisy / large but otherwise allowed.
SKIP_FILENAMES: frozenset[str] = frozenset(
    {
        ".DS_Store",
        "pnpm-lock.yaml",
        "package-lock.json",
        "Cargo.lock",
        "uv.lock",
        "poetry.lock",
    }
)

# Files we always highlight at the top of the map, in the order shown.
KEY_FILES: tuple[tuple[str, str], ...] = (
    ("AGENTS.md", "Agent entry point"),
    ("CLAUDE.md", "Claude Code operating manual"),
    ("CODEX.md", "Codex Windows app operating manual"),
    ("README.md", "Human-facing project README"),
    ("MAD.md", "Master architecture / vision (if present)"),
    ("pyproject.toml", "Python package + tool config"),
    (".agents/skills/prime/SKILL.md", "Codex prime skill"),
    (".codex/README.md", "Codex app local actions"),
    ("docs/agent/CONTEXT_BRIEF.md", "One-page repo snapshot"),
    ("docs/agent/HANDOFF.md", "Last-session state"),
    ("docs/agent/WORKFLOW.md", "Default agent loop"),
    ("docs/agent/PLANS.md", "ExecPlan convention"),
    ("docs/agent/QUALITY.md", "Known weak areas / debt"),
    ("docs/architecture/ARCHITECTURE.md", "Architecture overview"),
    ("docs/contracts/worker-protocol.md", "Worker JSON protocol"),
    ("docs/contracts/worker-events.schema.json", "Event JSON schema"),
    ("docs/contracts/worker-requests.schema.json", "Request JSON schema"),
    ("srtforge/cli.py", "Typer CLI + worker JSON loop"),
    ("srtforge/pipeline.py", "Ordered processing chain"),
    ("srtforge/logging.py", "RunLogger + stage event emitter"),
    ("srtforge/settings.py", "Settings dataclass tree"),
    ("srtforge/config.py", "PROJECT_ROOT / models resolution"),
    ("srtforge/worker_protocol.py", "Typed worker request/event helpers"),
    ("srtforge/mux.py", "Embed / burn helpers"),
    ("srtforge-studio/src/types.ts", "TypeScript worker contract"),
    ("srtforge-studio/src/store.ts", "Zustand reducer for worker events"),
    ("srtforge-studio/src-tauri/src/lib.rs", "Tauri shell + worker child"),
    ("scripts/check.ps1", "Lightweight harness (Windows)"),
    ("scripts/check.sh", "Lightweight harness (Unix)"),
    ("scripts/codex_prime.py", "Read-only Codex startup bundle"),
    ("scripts/doctor.py", "Environment report"),
    ("scripts/update_context.py", "This file"),
    ("scripts/check_docs.py", "Doc freshness check"),
    ("tests/test_cli_worker.py", "Worker protocol tests"),
    ("tests/test_pipeline.py", "Pipeline tests"),
    ("tests/test_worker_protocol.py", "Typed worker protocol contract tests"),
)

# Top-level directories whose immediate contents (one level deep) we
# always want represented even if the recursion limit would prune them.
ALWAYS_DESCEND: frozenset[str] = frozenset(
    {
        "srtforge",
        "srtforge-studio",
        "tests",
        "docs",
        ".claude",
        ".agents",
        ".codex",
        ".github",
        "scripts",
        "packaging",
    }
)


def _is_hidden(name: str) -> bool:
    return name.startswith(".") and name not in {
        ".github",
        ".claude",
        ".agents",
        ".codex",
        ".env.example",
    }


def _should_skip_dir(path: Path) -> bool:
    if path.name in SKIP_DIRS:
        return True
    return False


def _should_skip_file(path: Path) -> bool:
    if path.name in SKIP_FILENAMES:
        return True
    if _is_hidden(path.name):
        return True
    if path.suffix.lower() in SKIP_SUFFIXES:
        return True
    return False


def _walk(root: Path, max_depth: int = 4) -> Iterable[tuple[Path, int]]:
    """Yield ``(path, depth)`` pairs for everything not skipped."""

    def _recurse(current: Path, depth: int) -> Iterable[tuple[Path, int]]:
        if depth > max_depth:
            return
        try:
            entries = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except (PermissionError, OSError):
            return
        for entry in entries:
            if entry.is_dir():
                if _should_skip_dir(entry):
                    continue
                if _is_hidden(entry.name):
                    continue
                yield entry, depth
                # Always descend at least one level into the curated top-level dirs.
                next_depth = depth + 1
                yield from _recurse(entry, next_depth)
            else:
                if _should_skip_file(entry):
                    continue
                yield entry, depth

    yield from _recurse(root, 0)


def _render_tree(root: Path) -> list[str]:
    lines: list[str] = ["```"]
    lines.append(f"{root.name}/")
    for path, depth in _walk(root):
        rel = path.relative_to(root)
        indent = "  " * (depth + 1)
        marker = "/" if path.is_dir() else ""
        lines.append(f"{indent}{rel.name}{marker}")
    lines.append("```")
    return lines


def _render_key_files(root: Path) -> list[str]:
    lines: list[str] = []
    for rel, label in KEY_FILES:
        path = root / rel
        if path.exists():
            lines.append(f"- `{rel}` — {label}")
        else:
            lines.append(f"- `{rel}` — {label} _(missing)_")
    return lines


def main() -> int:
    if not REPO_ROOT.exists():
        print(f"Repo root not found: {REPO_ROOT}", file=sys.stderr)
        return 1

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body: list[str] = [
        "# PROJECT_MAP.md",
        "",
        f"_Generated by `scripts/update_context.py` on {timestamp}._",
        "",
        "Compact map of the repo's important files and a curated tree.",
        "Regenerate with:",
        "",
        "```powershell",
        "python scripts/update_context.py",
        "```",
        "",
        "## Key files",
        "",
        *_render_key_files(REPO_ROOT),
        "",
        "## Tree (depth-limited, generated artifacts excluded)",
        "",
        *_render_tree(REPO_ROOT),
        "",
        "## What's intentionally excluded",
        "",
        "- Heavy generated dirs: `.venv/`, `node_modules/`, `target/`, "
        "`dist/`, `build/`, `models/`, `logs/`, `output/`, `tmp/`, `gen/`.",
        "- Lockfiles: `pnpm-lock.yaml`, `Cargo.lock`, `package-lock.json`.",
        "- Native binaries / model checkpoints: `.exe`, `.dll`, `.pyd`, `.so`, "
        "`.onnx`, `.ckpt`, `.pt`, `.bin`.",
        "- Media: `.mkv`, `.mp4`, `.webm`, `.mp3`, `.wav`, `.flac`, `.srt`.",
        "- Hidden files except `.github/`, `.claude/`, `.agents/`, `.codex/`, "
        "and `.env.example`.",
        "",
        "If the map is missing something you need, edit "
        "`scripts/update_context.py` (`KEY_FILES` / `SKIP_*`) and rerun.",
        "",
    ]

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text("\n".join(body), encoding="utf-8", newline="\n")
    rel = OUTPUT.relative_to(REPO_ROOT)
    print(f"Wrote {rel} ({OUTPUT.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
