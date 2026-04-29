#!/usr/bin/env python
"""Doc freshness / required-section check.

Reads-only. Exits non-zero when:

- ``AGENTS.md`` references a missing file,
- the Codex operating layer is missing,
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
        "CODEX.md",
        ".codex/README.md",
        ".codex/actions/harness-check.ps1",
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

REQUIRED_CODEX_SKILLS: tuple[str, ...] = (
    "prime",
    "plan",
    "execplan",
    "fix-loop",
    "harness-check",
    "handoff",
    "pr-summary",
    "protocol-change",
    "repo-map",
    "limit-safe-stop",
    "cross-agent-review",
    "push-safe",
)

AGENTS_REQUIRED_LINKS: tuple[str, ...] = (
    "CODEX.md",
    ".agents/skills/",
    ".codex/",
)

CODEX_REQUIRED_LINKS: tuple[str, ...] = (
    "AGENTS.md",
    "docs/agent/HANDOFF.md",
    "docs/agent/WORKFLOW.md",
    "scripts/check.ps1",
)

STALE_CHECK_SKIP_PARTS: frozenset[str] = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        "target",
        "dist",
        "build",
    }
)

STALE_CHECK_SKIP_SUFFIXES: frozenset[str] = frozenset(
    {
        ".pyc",
        ".pyo",
        ".log",
        ".exe",
        ".dll",
        ".pyd",
        ".so",
        ".dylib",
    }
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


def _has_skill_metadata(text: str, field: str) -> bool:
    if not text.startswith("---"):
        return False
    end = text.find("\n---", 3)
    if end == -1:
        return False
    front_matter = text[3:end]
    return bool(re.search(rf"^{re.escape(field)}:\s*\S", front_matter, flags=re.MULTILINE))


def _is_source_for_staleness(path: Path) -> bool:
    if path.name.startswith("."):
        return False
    if any(part in STALE_CHECK_SKIP_PARTS for part in path.parts):
        return False
    if path.suffix.lower() in STALE_CHECK_SKIP_SUFFIXES:
        return False
    return True


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
        for required_link in AGENTS_REQUIRED_LINKS:
            if required_link not in text:
                errors.append(
                    f"AGENTS.md missing required Codex link: {required_link}"
                )
        for link in _agents_md_links(text):
            # Only check obvious in-repo paths, not external references.
            if link.startswith(("http", "https", "://")):
                continue
            target = REPO_ROOT / link
            if not target.exists():
                errors.append(
                    f"AGENTS.md references missing path: {link}"
                )

    # 2b. CODEX.md has the minimum operating links
    codex_md = REPO_ROOT / "CODEX.md"
    if codex_md.exists():
        text = _read(codex_md)
        for required_link in CODEX_REQUIRED_LINKS:
            if required_link not in text:
                errors.append(
                    f"CODEX.md missing required link/reference: {required_link}"
                )

    # 2c. Codex repo-scoped skills exist and carry basic metadata
    codex_skills_dir = REPO_ROOT / ".agents" / "skills"
    if not codex_skills_dir.exists():
        errors.append("missing Codex skills directory: .agents/skills/")
    else:
        for skill in REQUIRED_CODEX_SKILLS:
            skill_md = codex_skills_dir / skill / "SKILL.md"
            if not skill_md.exists():
                errors.append(f"missing Codex skill: .agents/skills/{skill}/SKILL.md")
                continue
            text = _read(skill_md)
            for field in ("name", "description"):
                if not _has_skill_metadata(text, field):
                    errors.append(
                        f"Codex skill {skill_md.relative_to(REPO_ROOT)} missing "
                        f"metadata field: {field}:"
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
                    if path.is_file() and _is_source_for_staleness(path):
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
