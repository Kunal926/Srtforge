#!/usr/bin/env python
"""Doc freshness / required-section check.

Reads-only. Exits non-zero when:

- ``AGENTS.md`` references a missing file,
- a required document is missing,
- ``docs/agent/HANDOFF.md`` lacks a required section,
- ``docs/agent/PROJECT_MAP.md`` is missing or stale (older than the
  newest tracked file under ``srtforge/`` / ``srtforge-studio/src/``),
- an active ExecPlan lacks a required section.

Run:

    python scripts/check_docs.py

Stays simple on purpose. Add new rules conservatively — every false
positive trains the agent to ignore the script.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES: tuple[Path, ...] = tuple(
    REPO_ROOT / p
    for p in (
        "AGENTS.md",
        "CLAUDE.md",
        "docs/agent/README.md",
        "docs/agent/CONTEXT_BRIEF.md",
        "docs/agent/HANDOFF.md",
        "docs/agent/WORKFLOW.md",
        "docs/agent/PLANS.md",
        "docs/agent/TASK_TEMPLATE.md",
        "docs/agent/QUALITY.md",
        "docs/agent/EXTERNAL_READING_SUMMARY.md",
        "docs/architecture/ARCHITECTURE.md",
        "docs/contracts/worker-protocol.md",
        "docs/adr/README.md",
        "docs/adr/ADR_TEMPLATE.md",
        "docs/adr/0001-agent-generated-development.md",
    )
)

# Sections required to appear in HANDOFF.md (markdown ## headings).
HANDOFF_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Current goal",
    "Current branch / git state",
    "Changed files",
    "Commands run and results",
    "Skipped checks and why",
    "Decisions made",
    "Known blockers",
    "Next recommended action",
)

# Sections required in any ExecPlan under exec-plans/active/.
EXECPLAN_REQUIRED_SECTIONS: tuple[str, ...] = (
    "Purpose",
    "Context",
    "Progress",
    "Steps",
    "Validation",
    "Decision log",
    "Outcomes",
)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _has_section(text: str, name: str) -> bool:
    pattern = rf"^##\s+.*\b{re.escape(name)}\b"
    return bool(re.search(pattern, text, flags=re.MULTILINE | re.IGNORECASE))


def _agents_md_links(text: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"`([^`]+\.(?:md|json|yml|yaml|toml|py|ts|tsx|rs))`", text):
        link = match.group(1)
        # Skip template placeholders (e.g. `docs/agent/exec-plans/active/<slug>.md`)
        # and shell glob/wildcards — those aren't real paths.
        if "<" in link or ">" in link or "*" in link or "?" in link:
            continue
        links.append(link)
    # Deduplicate, preserve order.
    seen: set[str] = set()
    unique: list[str] = []
    for link in links:
        if link in seen:
            continue
        seen.add(link)
        unique.append(link)
    return unique


def main() -> int:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Required files
    for path in REQUIRED_FILES:
        if not path.exists():
            errors.append(f"missing required doc: {path.relative_to(REPO_ROOT)}")

    # 2. AGENTS.md links resolve
    agents_md = REPO_ROOT / "AGENTS.md"
    if agents_md.exists():
        text = _read(agents_md)
        for link in _agents_md_links(text):
            # Only check obvious in-repo paths, not external references.
            if link.startswith(("http", "https", "://")):
                continue
            target = REPO_ROOT / link
            if not target.exists():
                errors.append(
                    f"AGENTS.md references missing path: {link}"
                )

    # 3. HANDOFF.md required sections
    handoff = REPO_ROOT / "docs" / "agent" / "HANDOFF.md"
    if handoff.exists():
        text = _read(handoff)
        for section in HANDOFF_REQUIRED_SECTIONS:
            if not _has_section(text, section):
                errors.append(
                    f"HANDOFF.md missing required section: '{section}'"
                )

    # 4. PROJECT_MAP.md presence (staleness check is best-effort)
    pmap = REPO_ROOT / "docs" / "agent" / "PROJECT_MAP.md"
    if not pmap.exists():
        errors.append(
            "docs/agent/PROJECT_MAP.md is missing — "
            "regenerate via `python scripts/update_context.py`."
        )
    else:
        try:
            map_mtime = pmap.stat().st_mtime
            newest = map_mtime
            for sub in ("srtforge", "srtforge-studio/src"):
                sub_root = REPO_ROOT / sub
                if not sub_root.exists():
                    continue
                for path in sub_root.rglob("*"):
                    if path.is_file() and not path.name.startswith("."):
                        try:
                            newest = max(newest, path.stat().st_mtime)
                        except OSError:
                            continue
            if newest > map_mtime + 60:
                warnings.append(
                    "docs/agent/PROJECT_MAP.md may be stale "
                    f"(newest source file is {int(newest - map_mtime)}s newer). "
                    "Regenerate via `python scripts/update_context.py`."
                )
        except OSError:
            pass

    # 5. Active ExecPlans have required sections
    active_plans_dir = REPO_ROOT / "docs" / "agent" / "exec-plans" / "active"
    if active_plans_dir.exists():
        for plan in active_plans_dir.glob("*.md"):
            if plan.name.lower() == "readme.md":
                continue
            text = _read(plan)
            for section in EXECPLAN_REQUIRED_SECTIONS:
                if not _has_section(text, section):
                    errors.append(
                        f"ExecPlan {plan.relative_to(REPO_ROOT)} missing "
                        f"required section: '{section}'"
                    )

    # 6. Worker protocol contract sanity
    proto = REPO_ROOT / "docs" / "contracts" / "worker-protocol.md"
    if proto.exists():
        text = _read(proto)
        for keyword in (
            "transcribe",
            "shutdown",
            "worker_starting",
            "worker_ready",
            "job_started",
            "job_failed",
            "srt_written",
        ):
            if keyword not in text:
                errors.append(
                    f"worker-protocol.md missing reference to '{keyword}'"
                )

    if warnings:
        print("Warnings:")
        for w in warnings:
            print(f"  - {w}")

    if errors:
        print("Errors:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    print("docs check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
