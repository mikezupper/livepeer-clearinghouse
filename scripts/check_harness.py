#!/usr/bin/env python3
"""Validate the repository's agent-first knowledge harness."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REQUIRED_FILES = (
    "AGENTS.md",
    "ARCHITECTURE.md",
    "CHANGELOG.md",
    ".codex/config.toml",
    ".codex/hooks.json",
    ".agents/skills/beads/SKILL.md",
    ".agents/skills/effect-fp/SKILL.md",
    ".agents/skills/lit-web-apps/SKILL.md",
    ".agents/skills/modern-css/SKILL.md",
    ".agents/skills/semantic-html/SKILL.md",
    "docs/index.md",
    "docs/product-specs/index.md",
    "docs/product-specs/walking-slice.md",
    "docs/design-docs/index.md",
    "docs/design-docs/core-beliefs.md",
    "docs/design-docs/adapter-loading.md",
    "docs/design-docs/remote-signer-metering.md",
    "docs/FRONTEND.md",
    "docs/QUALITY.md",
    "docs/SECURITY.md",
    "docs/security/threat-model.md",
    "docs/security/data-lifecycle.md",
    "docs/RELIABILITY.md",
    "docs/RELEASING.md",
    "docs/WORK_TRACKING.md",
)
FORBIDDEN_TRACKERS = ("TODO.md", "PLAN.md", "PLANS.md", "docs/exec-plans")
REQUIRED_HOOKS = {
    "SessionStart",
    "PreCompact",
    "PostCompact",
    "UserPromptSubmit",
}
MARKDOWN_LINK = re.compile(r"\[[^]]+\]\(([^)]+)\)")


def markdown_files(root: Path) -> tuple[Path, ...]:
    """Return canonical Markdown files that must have valid local links."""
    top_level = tuple(root / name for name in ("AGENTS.md", "ARCHITECTURE.md"))
    return top_level + tuple(sorted((root / "docs").rglob("*.md")))


def validate(root: Path) -> list[str]:
    """Return human-readable invariant violations for a repository root."""
    errors: list[str] = []

    for relative in REQUIRED_FILES:
        if not (root / relative).is_file():
            errors.append(f"missing required file: {relative}")

    for relative in FORBIDDEN_TRACKERS:
        if (root / relative).exists():
            errors.append(f"parallel work tracker is forbidden: {relative}")

    agents = root / "AGENTS.md"
    if agents.is_file() and len(agents.read_text(encoding="utf-8").splitlines()) > 100:
        errors.append("AGENTS.md must remain at or below 100 lines")

    hooks_path = root / ".codex/hooks.json"
    if hooks_path.is_file():
        try:
            hooks = json.loads(hooks_path.read_text(encoding="utf-8"))["hooks"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            errors.append(f"invalid .codex/hooks.json: {error}")
        else:
            missing_hooks = REQUIRED_HOOKS - set(hooks)
            if missing_hooks:
                errors.append(f"missing Codex hooks: {', '.join(sorted(missing_hooks))}")

    for document in markdown_files(root):
        if not document.is_file():
            continue
        for target in MARKDOWN_LINK.findall(document.read_text(encoding="utf-8")):
            if "://" in target or target.startswith(("#", "mailto:")):
                continue
            path_target = target.split("#", maxsplit=1)[0]
            if path_target and not (document.parent / path_target).resolve().exists():
                errors.append(f"broken local link in {document.relative_to(root)}: {target}")

    return errors


def main() -> int:
    """Run the harness validation from the repository containing this script."""
    root = Path(__file__).resolve().parents[1]
    errors = validate(root)
    if errors:
        for error in errors:
            print(f"harness: {error}", file=sys.stderr)
        return 1
    print("harness: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
