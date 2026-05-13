#!/usr/bin/env python
"""Public documentation and contract sanity checks.

The check is read-only. It verifies that the public docs needed by users and
contributors exist, that the worker protocol docs and schemas still mention the
known request/event vocabulary, and that old private operating-layer references
do not reappear in public files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "requirements.txt",
    "install.ps1",
    "install.sh",
    ".github/workflows/harness.yml",
    "docs/architecture/ARCHITECTURE.md",
    "docs/contracts/worker-protocol.md",
    "docs/contracts/worker-events.schema.json",
    "docs/contracts/worker-requests.schema.json",
    "docs/adr/README.md",
    "docs/adr/ADR_TEMPLATE.md",
    "srtforge-studio/README.md",
)

README_REQUIRED_TERMS: tuple[str, ...] = (
    "Parakeet",
    "Faster-Whisper",
    "FV4",
    "Srtforge Studio",
    "srtforge run",
    "install.ps1",
    "install.sh",
    "gpu-smoke",
    "CUDA 12.8",
)

STUDIO_README_REQUIRED_TERMS: tuple[str, ...] = (
    "Tauri 2",
    "React",
    "Zustand",
    "one-dir sidecar",
    "rebuild_studio_sidecar.ps1",
    "pnpm tauri dev",
    "worker --no-preload",
    "gpu-smoke",
)

WORKER_REQUESTS: tuple[str, ...] = (
    "transcribe",
    "normalize",
    "separate",
    "shutdown",
    "clear_gpu_cache",
)

WORKER_EVENTS: tuple[str, ...] = (
    "worker_starting",
    "worker_ready",
    "job_started",
    "stage",
    "progress",
    "log",
    "srt_written",
    "job_completed",
    "job_failed",
)

STALE_PRIVATE_REFERENCES: tuple[str, ...] = (
    "A" + "GENTS.md",
    "C" + "LAUDE.md",
    "C" + "ODEX.md",
    "." + "ag" + "ents/",
    "." + "cla" + "ude/",
    "." + "cod" + "ex/",
    "docs/" + "ag" + "ent/",
    "H" + "ANDOFF.md",
    "P" + "ROJECT_MAP.md",
    "E" + "xecPlan",
    "codex_" + "prime",
    "update_" + "context.py",
)

PUBLIC_TEXT_PATHS: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    ".gitignore",
    ".github/workflows/harness.yml",
    "docs/architecture/ARCHITECTURE.md",
    "docs/contracts/worker-protocol.md",
    "docs/adr/README.md",
    "docs/adr/ADR_TEMPLATE.md",
    "srtforge-studio/README.md",
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8", errors="replace")


def _load_json(relative_path: str) -> object:
    with (REPO_ROOT / relative_path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _missing_terms(text: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if term not in text]


def _schema_titles(schema: object) -> set[str]:
    titles: set[str] = set()

    def visit(value: object) -> None:
        if isinstance(value, dict):
            title = value.get("title")
            if isinstance(title, str):
                titles.add(title)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    return titles


def _adr_index_entries(text: str) -> list[str]:
    entries: list[str] = []
    for match in re.finditer(r"\|\s*(\d{4})\s*\|", text):
        entries.append(match.group(1))
    return entries


def main() -> int:
    errors: list[str] = []

    for relative_path in REQUIRED_FILES:
        if not (REPO_ROOT / relative_path).exists():
            errors.append(f"missing required public file: {relative_path}")

    if not errors:
        readme = _read("README.md")
        for term in _missing_terms(readme, README_REQUIRED_TERMS):
            errors.append(f"README.md missing current product term: {term}")

        studio_readme = _read("srtforge-studio/README.md")
        for term in _missing_terms(studio_readme, STUDIO_README_REQUIRED_TERMS):
            errors.append(f"srtforge-studio/README.md missing term: {term}")

        protocol = _read("docs/contracts/worker-protocol.md")
        for keyword in WORKER_REQUESTS + WORKER_EVENTS:
            if keyword not in protocol:
                errors.append(f"worker-protocol.md missing keyword: {keyword}")

        request_titles = _schema_titles(_load_json("docs/contracts/worker-requests.schema.json"))
        for request in WORKER_REQUESTS:
            if request not in request_titles:
                errors.append(f"worker-requests.schema.json missing request: {request}")

        event_titles = _schema_titles(_load_json("docs/contracts/worker-events.schema.json"))
        for event in WORKER_EVENTS:
            if event not in event_titles:
                errors.append(f"worker-events.schema.json missing event: {event}")

        adr_readme = _read("docs/adr/README.md")
        for number in _adr_index_entries(adr_readme):
            matches = list((REPO_ROOT / "docs" / "adr").glob(f"{number}-*.md"))
            if not matches:
                errors.append(f"docs/adr/README.md indexes missing ADR: {number}")

        for relative_path in PUBLIC_TEXT_PATHS:
            text = _read(relative_path)
            for stale in STALE_PRIVATE_REFERENCES:
                if stale in text:
                    errors.append(f"{relative_path} contains stale private reference: {stale}")

    if errors:
        print("Errors:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1

    print("docs check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
