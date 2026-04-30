#!/usr/bin/env python
"""Print a compact read-only startup bundle for Codex.

No network, no secrets, no model/media access, and no dependency on third-party
packages. This script only reads Git metadata and lightweight repo docs.

Run:

    python scripts/codex_prime.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

KEY_DOCS: tuple[str, ...] = (
    "AGENTS.md",
    "CODEX.md",
    "docs/agent/CONTEXT_BRIEF.md",
    "docs/agent/HANDOFF.md",
    "docs/agent/WORKFLOW.md",
    "docs/agent/PROJECT_MAP.md",
    ".agents/skills/prime/SKILL.md",
    ".codex/README.md",
)


def _run_git(args: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
    except OSError as exc:
        return 1, str(exc)
    return proc.returncode, proc.stdout.strip()


def _section_body(text: str, heading: str) -> str:
    marker = f"## {heading}"
    start = text.find(marker)
    if start == -1:
        return ""
    start = text.find("\n", start)
    if start == -1:
        return ""
    end = text.find("\n## ", start + 1)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def _next_recommended_action() -> str:
    handoff = REPO_ROOT / "docs" / "agent" / "HANDOFF.md"
    if not handoff.exists():
        return "HANDOFF.md missing"
    text = handoff.read_text(encoding="utf-8", errors="replace")
    body = _section_body(text, "Next recommended action")
    return " ".join(body.split()) if body else "No next action recorded"


def _active_execplans() -> list[str]:
    active = REPO_ROOT / "docs" / "agent" / "exec-plans" / "active"
    if not active.exists():
        return []
    return sorted(
        str(path.relative_to(REPO_ROOT))
        for path in active.glob("*.md")
        if path.name.lower() != "readme.md"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("Codex startup bundle")
    print("====================")

    code, branch = _run_git(["branch", "--show-current"])
    print(f"Branch: {branch if code == 0 and branch else 'unknown'}")

    code, status = _run_git(["status", "--short"])
    if code == 0:
        lines = status.splitlines()
        print(f"Git status: {'clean' if not lines else f'{len(lines)} changed entries'}")
        for line in lines[:20]:
            print(f"  {line}")
        if len(lines) > 20:
            print(f"  ... {len(lines) - 20} more")
    else:
        print(f"Git status: failed: {status}")

    code, log = _run_git(["log", "--oneline", "-5"])
    print("")
    print("Latest commits:")
    if code == 0 and log:
        for line in log.splitlines():
            print(f"  {line}")
    else:
        print(f"  failed: {log}")

    print("")
    print("Key docs:")
    for rel in KEY_DOCS:
        marker = "present" if (REPO_ROOT / rel).exists() else "missing"
        print(f"  {marker}: {rel}")

    print("")
    print("Active ExecPlans:")
    plans = _active_execplans()
    if plans:
        for plan in plans:
            print(f"  {plan}")
    else:
        print("  none")

    print("")
    print("Next recommended action:")
    print(f"  {_next_recommended_action()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
